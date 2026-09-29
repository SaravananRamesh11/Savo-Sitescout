"""Bin raw OSM / store / demographic data into the 500 m grid and compute per-cell + area features.

All data is fetched once for the whole area; this module only does local spatial maths, in
UTM metres, with STRtree indexes (no per-cell external calls).
"""
from collections import defaultdict

from shapely import STRtree
from shapely.geometry import LineString, Point
from shapely.prepared import prep

from app.core import scoring_constants as K
from app.core.config import CELL_SIZE_M
from app.services import demographics, grid
from app.services.overpass_client import MAJOR_ROADS

CELL_KM2 = (CELL_SIZE_M / 1000) ** 2
FEATURE_KEYS = ["pop_density", "household_density", "growth_pct", "road_km_per_km2", "major_road_dist_m",
                "transit_count", "commercial_per_km2", "amenity_units_per_km2", "competitor_count",
                "savomart_dist_m"]


def _pts(pois, cats):
    out = []
    for p in pois:
        if p["cat"] in cats:
            x, y = grid.lonlat_to_utm(p["lon"], p["lat"])
            out.append((Point(x, y), p))
    return out


def compute_cell_features(poly_ll, cells, osm: dict, stores: list[dict],
                          demo_map: dict | None = None) -> tuple[dict, dict]:
    """Returns (cell_features {cell_id: {...}}, raw_counts {totals used for the area profile})."""
    poly_utm = grid.to_utm(poly_ll)
    inside = prep(poly_utm)

    # ---- points, filtered to the polygon and assigned to exactly one cell (floor of UTM coords)
    binned: dict[tuple[int, int], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    totals: dict[str, int] = defaultdict(int)
    for p in osm["pois"]:
        x, y = grid.lonlat_to_utm(p["lon"], p["lat"])
        pt = Point(x, y)
        if not inside.contains(pt):
            continue
        cell = grid.cell_of_utm(x, y)
        binned[cell][p["cat"]] += 1
        totals[p["cat"]] += 1

    # ---- proximity indexes use ALL fetched points (the bbox is padded by 1 km)
    comp = _pts(osm["pois"], {"supermarket", "convenience"})
    transit = _pts(osm["pois"], {"transit"})
    comp_tree = STRtree([p for p, _ in comp]) if comp else None
    transit_tree = STRtree([p for p, _ in transit]) if transit else None

    lines, major = [], []
    for r in osm["roads"]:
        ln = grid.to_utm(LineString(r["coords"]))
        lines.append((ln, r))
        if r["hw"] in MAJOR_ROADS:
            major.append(ln)
    line_tree = STRtree([ln for ln, _ in lines]) if lines else None
    major_tree = STRtree(major) if major else None
    named = [(ln, r["name"]) for ln, r in lines if r.get("name")]
    named_tree = STRtree([ln for ln, _ in named]) if named else None

    store_pts = []
    for s in stores:
        x, y = grid.lonlat_to_utm(s["longitude"], s["latitude"])
        store_pts.append((Point(x, y), s))

    feats: dict[str, dict] = {}
    road_km_total = 0.0
    for col, row, cov in cells:
        cid = grid.make_cell_id(col, row)
        box = grid.cell_box_utm(col, row)
        cell_poly = box.intersection(poly_utm)
        area = max(cov * CELL_KM2, 0.001)
        centroid = box.centroid
        lon, lat = grid._to_ll.transform(centroid.x, centroid.y)
        demo = (demo_map or {}).get(cid) or demographics.estimate_at(lat, lon)
        counts = binned.get((col, row), {})

        road_len = 0.0
        if line_tree is not None:
            for i in line_tree.query(cell_poly):
                road_len += lines[i][0].intersection(cell_poly).length
        road_km = road_len / 1000
        road_km_total += road_km

        md = None
        if major_tree is not None:
            i = major_tree.nearest(centroid)
            md = float(centroid.distance(major[i]))
        tc = 0
        if transit_tree is not None:
            tc = len([1 for i in transit_tree.query(centroid.buffer(K.TRANSIT_RADIUS_M)) if
                      transit[i][0].distance(centroid) <= K.TRANSIT_RADIUS_M])
        cc = 0
        if comp_tree is not None:
            cc = len([1 for i in comp_tree.query(centroid.buffer(K.COMPETITION_RADIUS_M)) if
                      comp[i][0].distance(centroid) <= K.COMPETITION_RADIUS_M])
        sd, sname = None, None
        if store_pts:
            d, st = min(((centroid.distance(p), s) for p, s in store_pts), key=lambda t: t[0])
            sd, sname = float(d), st["name"]

        commercial = counts.get("commercial", 0) + counts.get("supermarket", 0) + counts.get("convenience", 0)
        amenity_units = sum(counts.get(k, 0) * w for k, w in K.AMENITY_WEIGHTS.items())
        road_name, road_dist = None, None
        if named_tree is not None:
            i = named_tree.nearest(centroid)
            road_name, road_dist = named[i][1], float(centroid.distance(named[i][0]))

        feats[cid] = {
            "cell_id": cid, "col": col, "row": row, "coverage": round(cov, 3),
            "lat": lat, "lon": lon,
            "pop_density": demo["pop_density"], "household_density": demo["household_density"],
            "growth_pct": demo["growth_pct"], "demo_locality": demo["nearest_locality"],
            "road_km_per_km2": road_km / area, "major_road_dist_m": md, "transit_count": tc,
            "commercial_per_km2": commercial / area, "amenity_units_per_km2": amenity_units / area,
            "competitor_count": cc, "savomart_dist_m": sd, "nearest_savomart": sname,
            "counts": dict(counts), "nearest_named_road": road_name, "nearest_named_road_m": road_dist,
        }
    totals["road_km"] = round(road_km_total, 2)
    totals["major_road_km"] = round(sum(
        ln.intersection(poly_utm).length for ln in major) / 1000, 2) if major else 0.0
    return feats, dict(totals)


def aggregate_area(feats: dict[str, dict]) -> dict:
    """Coverage-weighted mean of cell features (so the area score uses the same formula/path)."""
    total_w = sum(f["coverage"] for f in feats.values()) or 1.0
    agg: dict = {}
    for k in FEATURE_KEYS:
        vals = [(f[k], f["coverage"]) for f in feats.values() if f[k] is not None]
        if not vals:
            agg[k] = None
            continue
        w = sum(c for _, c in vals)
        agg[k] = sum(v * c for v, c in vals) / w
    agg["_coverage_weight"] = total_w
    return agg
