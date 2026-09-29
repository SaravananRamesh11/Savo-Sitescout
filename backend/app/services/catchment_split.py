"""Deterministic, non-overlapping, workload-balanced split of a catchment into survey work units.

Heuristic on purpose (no optimiser):
1. Partition the study polygon exactly into BLOCK_M blocks (UTM metres).
2. Score each block: lane length + commercial POIs + amenity POIs + estimated households (M1 estimate, flagged).
3. Order blocks in a serpentine sweep and cut where cumulative workload crosses k * total / K.
4. Each unit is the union of its blocks, so units never overlap and leave no gaps by construction.

Lane count alone is never the balance signal. OSM buildings are not fetched, so building density is not claimed.
Mock data is never used to balance work: without real OSM data the split falls back to equal-area blocks and says so.
"""
from __future__ import annotations

import math
import time
from collections import defaultdict

from shapely import STRtree
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box
from shapely.ops import unary_union
from sqlalchemy.orm import Session

from app.core import survey_constants as K
from app.models.db_models import ExternalDataCache
from app.services import demographics, grid, overpass_client

COMMERCIAL = {"commercial", "supermarket", "convenience"}
AMENITY = {"school", "college", "hospital", "clinic", "transit"}

# The OSM payload used for a preview is kept briefly so the confirmed split is identical to the one the manager saw.
_OSM_MEMO: dict[int, tuple[float, dict | None, list[str]]] = {}
_MEMO_TTL_S = 900


def _covers(payload: dict, poly_ll) -> bool:
    if not payload or payload.get("mock") or payload.get("pois") is None or not payload.get("bbox"):
        return False
    s, w, n, e = payload["bbox"]
    minx, miny, maxx, maxy = poly_ll.bounds
    return s <= miny and w <= minx and n >= maxy and e >= maxx


def load_osm(db: Session, study_id: int, poly_ll, area_id: int | None, *, fresh: bool = False):
    """Real OSM data for the study, or (None, flags) when unavailable. Never mock data."""
    now = time.time()
    if not fresh and study_id in _OSM_MEMO and now - _OSM_MEMO[study_id][0] < _MEMO_TTL_S:
        return _OSM_MEMO[study_id][1], list(_OSM_MEMO[study_id][2])
    payload, flags = None, []
    if area_id is not None:
        row = db.query(ExternalDataCache).filter_by(area_id=area_id, source="overpass").first()
        if row is not None and _covers(row.payload, poly_ll):
            payload = row.payload
    if payload is None:
        minx, miny, maxx, maxy = poly_ll.bounds
        pad = 0.0009  # about 100 m so roads crossing the edge are complete
        try:
            payload = overpass_client.fetch_bbox(miny - pad, minx - pad, maxy + pad, maxx + pad)
        except Exception:  # noqa: BLE001
            payload, flags = None, ["split_by_area_only"]
    _OSM_MEMO[study_id] = (now, payload, flags)
    return payload, list(flags)


def _lines(payload: dict | None):
    out = []
    for r in (payload or {}).get("roads", []):
        try:
            out.append((grid.to_utm(LineString(r["coords"])), r.get("name"), r.get("hw")))
        except Exception:  # noqa: BLE001
            continue
    return out


def compute_split(poly_ll: Polygon, payload: dict | None, units: int | None = None) -> dict:
    """Returns {"units": [...], "meta": {...}}. `units` overrides the automatic unit count."""
    poly = grid.to_utm(poly_ll)
    bs = K.BLOCK_M
    minx, miny, maxx, maxy = poly.bounds
    c0, c1 = math.floor(minx / bs), math.floor(maxx / bs)
    r0, r1 = math.floor(miny / bs), math.floor(maxy / bs)

    blocks = []  # (row, col, geom)
    for r in range(r0, r1 + 1):
        for c in range(c0, c1 + 1):
            g = box(c * bs, r * bs, (c + 1) * bs, (r + 1) * bs).intersection(poly)
            if g.is_empty or g.area < 1.0:
                continue
            blocks.append((r, c, g))
    if not blocks:
        raise ValueError("The study area is empty")

    has_osm = payload is not None
    lines = _lines(payload) if has_osm else []
    line_tree = STRtree([ln for ln, _, _ in lines]) if lines else None
    pois_by_cell: dict[tuple[int, int], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    if has_osm:
        for p in payload.get("pois", []):
            x, y = grid.lonlat_to_utm(p["lon"], p["lat"])
            if poly.contains(Point(x, y)):
                pois_by_cell[(math.floor(y / bs), math.floor(x / bs))][p["cat"]] += 1

    metrics = []
    for r, c, g in blocks:
        road_m, lanes = 0.0, defaultdict(float)
        if line_tree is not None:
            for i in line_tree.query(g):
                ln, name, _hw = lines[i]
                seg = ln.intersection(g).length
                if seg > 0:
                    road_m += seg
                    if name:
                        lanes[name] += seg
        cats = pois_by_cell.get((r, c), {})
        comm = sum(v for k_, v in cats.items() if k_ in COMMERCIAL)
        amen = sum(v for k_, v in cats.items() if k_ in AMENITY)
        cen = g.centroid
        lon, lat = grid._to_ll.transform(cen.x, cen.y)
        hh = demographics.estimate_at(lat, lon)["household_density"] * g.area / 1e6  # ESTIMATED households
        if has_osm:
            pts = (road_m / 100) * K.W_ROAD_PER_100M + comm * K.W_COMMERCIAL_POI + amen * K.W_AMENITY_POI \
                + hh / K.HOUSEHOLDS_PER_POINT
        else:
            pts = g.area / (bs * bs)  # equal-area fallback: one point per full block
        metrics.append({"road_m": road_m, "lanes": lanes, "commercial": comm, "amenity": amen, "households": hh,
                        "points": pts})

    total = sum(m["points"] for m in metrics)
    if units is not None:
        k = max(1, min(int(units), K.MAX_UNITS, len(blocks)))
    else:
        k = max(1, min(round(total / K.TARGET_POINTS_PER_UNIT), K.MAX_UNITS, len(blocks)))

    # serpentine order: rows south -> north, alternate direction each row so consecutive blocks stay adjacent
    order = sorted(range(len(blocks)), key=lambda i: (blocks[i][0], blocks[i][1] if blocks[i][0] % 2 == 0 else -blocks[i][1]))
    groups: list[list[int]] = [[] for _ in range(k)]
    cum = 0.0
    for i in order:
        mid = cum + metrics[i]["points"] / 2
        idx = min(k - 1, int(mid / (total / k))) if total > 0 else 0
        groups[idx].append(i)
        cum += metrics[i]["points"]
    # never leave a unit empty (very uneven blocks): move the last block of the largest group
    for gi in range(k):
        while not groups[gi]:
            big = max(range(k), key=lambda j: len(groups[j]))
            groups[gi].append(groups[big].pop())
    groups = [g for g in groups if g]

    out = []
    for n, g in enumerate(groups, 1):
        geom = unary_union([blocks[i][2] for i in g])
        if isinstance(geom, Polygon):
            geom = MultiPolygon([geom])
        pts = sum(metrics[i]["points"] for i in g)
        road = sum(metrics[i]["road_m"] for i in g)
        lanes: dict[str, float] = defaultdict(float)
        for i in g:
            for name, ln in metrics[i]["lanes"].items():
                lanes[name] += ln
        top = sorted(lanes.items(), key=lambda kv: -kv[1])[:8]
        target = int(min(K.MAX_TARGET_CAPTURES, max(K.MIN_TARGET_CAPTURES, round(pts * K.CAPTURES_PER_POINT))))
        out.append({
            "index": n, "unit_code": f"U{n:02d}", "geometry_ll": grid.to_ll(geom),
            "estimated_distance_m": round(road) if has_osm else None, "target_capture_count": target,
            "workload": {
                "points": round(pts, 1), "blocks": len(g), "area_m2": round(geom.area),
                "road_m": round(road) if has_osm else None, "lanes": [{"name": nm, "length_m": round(l)} for nm, l in top],
                "commercial_pois": sum(metrics[i]["commercial"] for i in g) if has_osm else None,
                "amenity_pois": sum(metrics[i]["amenity"] for i in g) if has_osm else None,
                "estimated_households": round(sum(metrics[i]["households"] for i in g)),
                "households_are_estimates": True,
                "basis": "roads, POIs and estimated households" if has_osm else "equal area (no road data available)",
            },
        })
    pts_list = [u["workload"]["points"] for u in out]
    meta = {
        "unit_count": len(out), "total_points": round(total, 1), "auto_units": units is None,
        "balance_ratio": round(max(pts_list) / max(min(pts_list), 1e-9), 2) if len(pts_list) > 1 else 1.0,
        "flags": [] if has_osm else ["split_by_area_only"],
        "study_area_km2": round(poly.area / 1e6, 3),
    }
    return {"units": out, "meta": meta}


def suggest_assignees(units: list[dict], executives: list[dict], open_load: dict[str, float]) -> list[str]:
    """Heaviest unit first goes to the currently least-loaded executive (ties by persona id)."""
    load = {e["id"]: float(open_load.get(e["id"], 0.0)) for e in executives}
    result = {}
    for u in sorted(units, key=lambda u: (-u["workload"]["points"], u["index"])):
        who = min(load, key=lambda k_: (load[k_], k_))
        result[u["index"]] = who
        load[who] += u["workload"]["points"]
    return [result[u["index"]] for u in units]
