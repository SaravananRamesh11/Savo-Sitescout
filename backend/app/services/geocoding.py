"""Resolve pincode / locality name / grid cells to a polygon.

* pincode  -> backend/data/chennai_pincodes.geojson (built by scripts/ingest_pincodes.py, no runtime
              network call). Falls back to Nominatim centroid + radius, labelled approximate.
* name     -> Nominatim (usage policy: custom UA, <= 1 req/s, results cached in `areas`), bounded to Chennai.
              A boundary polygon is used when OSM has one, else centroid + radius (approximate).
* grid     -> union of selected 500 m cells (contiguous, max 100).
"""
import json
import re
import threading
import time
from functools import lru_cache
from pathlib import Path

import httpx
from shapely.geometry import Point, Polygon, shape

from app.core.config import MAX_AREA_KM2, MAX_GRID_CELLS, NOMINATIM_URL, USER_AGENT
from app.services import grid

PINCODE_FILE = Path(__file__).resolve().parents[2] / "data" / "chennai_pincodes.geojson"
CHENNAI_BBOX = (79.90, 12.70, 80.45, 13.35)  # minlon, minlat, maxlon, maxlat
_lock = threading.Lock()
_last_call = 0.0


class AreaResolveError(ValueError):
    """User-facing problem with the requested area (mapped to HTTP 422)."""


def _nominatim(path: str, params: dict) -> list[dict]:
    global _last_call
    last = "unknown error"
    for attempt in range(3):
        with _lock:  # respect the 1 req/s usage policy
            wait = 1.1 - (time.time() - _last_call)
            if wait > 0:
                time.sleep(wait)
            try:
                r = httpx.get(f"{NOMINATIM_URL}/{path}", params=params, headers={"User-Agent": USER_AGENT},
                              timeout=30)
            except httpx.HTTPError as exc:
                r, last = None, f"unreachable ({exc})"
            finally:
                _last_call = time.time()
        if r is not None and r.status_code == 200:
            return r.json()
        if r is not None:
            last = f"HTTP {r.status_code}"
            if r.status_code not in (429, 502, 503, 504):
                break
        time.sleep(3 * (attempt + 1))
    raise AreaResolveError(f"Geocoder {last}; try a pincode, a known locality, or pick grid cells on the map")


def _local_locality(q: str) -> dict | None:
    """Offline fallback: match the name against the bundled locality table (centroid + radius)."""
    from app.services import demographics

    ql = q.lower().strip()
    for z in demographics.load_mock()["zones"]:
        if ql == z["name"].lower() or (len(ql) >= 4 and ql in z["name"].lower()):
            return {"polygon": _circle_ll(z["lon"], z["lat"], 1200), "name": z["name"], "quality": "approximate",
                    "note": "geocoder unavailable; centroid + 1.2 km radius from the bundled locality table"}
    return None


def _in_chennai(lon: float, lat: float) -> bool:
    return CHENNAI_BBOX[0] <= lon <= CHENNAI_BBOX[2] and CHENNAI_BBOX[1] <= lat <= CHENNAI_BBOX[3]


def _circle_ll(lon: float, lat: float, radius_m: float) -> Polygon:
    x, y = grid.lonlat_to_utm(lon, lat)
    return grid.to_ll(Point(x, y).buffer(radius_m, 24))


def _locality_from_label(label: str) -> str:
    parts = [p.strip() for p in label.split(",")]
    return parts[1] if len(parts) > 1 else "Chennai"


def _largest_polygon(geom) -> Polygon:
    if geom.geom_type == "Polygon":
        return geom
    return max(geom.geoms, key=lambda g: g.area)


@lru_cache(maxsize=1)
def _pincode_index() -> dict[str, dict]:
    if not PINCODE_FILE.exists():
        return {}
    fc = json.loads(PINCODE_FILE.read_text(encoding="utf-8"))
    return {f["properties"]["pincode"]: f for f in fc["features"]}


def resolve_pincode(pin: str) -> dict:
    pin = pin.strip()
    if not re.fullmatch(r"\d{6}", pin):
        raise AreaResolveError("A pincode is 6 digits, e.g. 600042")
    feat = _pincode_index().get(pin)
    if feat:
        poly = _largest_polygon(shape(feat["geometry"]))
        quality = "approximate" if feat["properties"].get("approximate") else "exact"
        name = f"{pin} · {_locality_from_label(feat['properties'].get('label', ''))}"
        return {"polygon": poly, "name": name, "quality": quality, "note": "boundary from local pincode file"}
    rows = _nominatim("search", {"postalcode": pin, "countrycodes": "in", "format": "jsonv2", "limit": 1})
    if not rows:
        raise AreaResolveError(f"Pincode {pin} not found. Try a locality name or pick grid cells on the map")
    lon, lat = float(rows[0]["lon"]), float(rows[0]["lat"])
    if not _in_chennai(lon, lat):
        raise AreaResolveError(f"Pincode {pin} is outside the Chennai region this product covers")
    return {"polygon": _circle_ll(lon, lat, 1500),
            "name": f"{pin} · {_locality_from_label(rows[0].get('display_name', ''))}",
            "quality": "approximate", "note": "centroid + 1.5 km radius (no boundary available)"}


def resolve_name(q: str) -> dict:
    q = q.strip()
    if len(q) < 3:
        raise AreaResolveError("Enter at least 3 characters of a locality name")
    vb = f"{CHENNAI_BBOX[0]},{CHENNAI_BBOX[3]},{CHENNAI_BBOX[2]},{CHENNAI_BBOX[1]}"
    try:
        rows = _nominatim("search", {"q": f"{q}, Chennai", "format": "jsonv2", "polygon_geojson": 1, "limit": 5,
                                     "viewbox": vb, "bounded": 1, "countrycodes": "in"})
    except AreaResolveError:
        local = _local_locality(q)
        if local:
            return local
        raise
    if not rows:
        raise AreaResolveError(f"'{q}' was not found in Chennai. Check the spelling, or try a pincode")
    poly_row = next((r for r in rows if r.get("geojson", {}).get("type") in ("Polygon", "MultiPolygon")), None)
    row = poly_row or rows[0]
    name = row.get("name") or q
    if poly_row:
        poly = _largest_polygon(shape(poly_row["geojson"]))
        quality, note = "exact", "boundary polygon from OpenStreetMap"
    else:
        poly = _circle_ll(float(row["lon"]), float(row["lat"]), 1200)
        quality, note = "approximate", "centroid + 1.2 km radius (OSM has no boundary polygon)"
    return {"polygon": poly, "name": name, "quality": quality, "note": note}


def resolve_cells(cell_ids: list[str]) -> dict:
    if not cell_ids:
        raise AreaResolveError("Select at least one grid cell")
    if len(cell_ids) > MAX_GRID_CELLS:
        raise AreaResolveError(f"Select at most {MAX_GRID_CELLS} cells (you selected {len(cell_ids)})")
    try:
        cells = sorted({grid.parse_cell_id(c) for c in cell_ids})
    except ValueError as exc:
        raise AreaResolveError(str(exc))
    if not grid.is_contiguous(cells):
        raise AreaResolveError("Selected cells must be connected (edge to edge). Remove or add cells to join them")
    poly = grid.union_of_cells_ll(cells)
    c = poly.centroid
    if not _in_chennai(c.x, c.y):
        raise AreaResolveError("Selected cells are outside the Chennai region")
    return {"polygon": poly, "name": f"Custom selection ({len(cells)} cells)", "quality": "exact",
            "note": "union of selected 500 m grid cells",
            "raw_input": ",".join(grid.make_cell_id(*c) for c in cells)}


def validate_size(poly) -> float:
    km2 = grid.area_km2(poly)
    if km2 > MAX_AREA_KM2:
        raise AreaResolveError(
            f"Area is {km2:.0f} km², above the {MAX_AREA_KM2:.0f} km² limit for one analysis. "
            "Pick a smaller locality, a pincode, or a few grid cells")
    if km2 < 0.05:
        raise AreaResolveError("Area is too small to analyse")
    return km2
