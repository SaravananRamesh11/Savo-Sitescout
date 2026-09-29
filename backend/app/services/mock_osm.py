"""Deterministic synthetic OSM payload, used ONLY when Overpass is unreachable and no cache exists.

The report is flagged `osm_mocked` so nobody mistakes it for real data. Densities are derived
from the (also estimated) demographic table so results stay plausible for a live demo.
"""
import hashlib
import random

from app.services import demographics, grid


def mock_osm_for_bbox(south: float, west: float, north: float, east: float) -> dict:
    seed = int(hashlib.md5(f"{south:.4f}{west:.4f}{north:.4f}{east:.4f}".encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)
    x0, y0 = grid.lonlat_to_utm(west, south)
    x1, y1 = grid.lonlat_to_utm(east, north)
    area_km2 = max((x1 - x0) * (y1 - y0) / 1e6, 0.01)
    demo = demographics.estimate_at((south + north) / 2, (west + east) / 2)
    intensity = demo["pop_density"] / 20_000  # 1.0 == typical Chennai neighbourhood
    rates = {"commercial": 260 * intensity, "school": 1.4 * intensity, "college": 0.25 * intensity,
             "hospital": 0.15 * intensity, "clinic": 2.0 * intensity, "supermarket": 0.7 * intensity,
             "convenience": 1.6 * intensity, "transit": 3.0}
    pois = []
    for cat, per_km2 in rates.items():
        for _ in range(int(per_km2 * area_km2)):
            x, y = rng.uniform(x0, x1), rng.uniform(y0, y1)
            lon, lat = grid._to_ll.transform(x, y)
            pois.append({"lon": lon, "lat": lat, "cat": cat, "name": None})
    roads = []
    step = 250.0
    x = x0
    while x <= x1:
        a, b = grid._to_ll.transform(x, y0), grid._to_ll.transform(x, y1)
        roads.append({"coords": [list(a), list(b)], "hw": "residential", "name": None})
        x += step
    y = y0
    k = 0
    while y <= y1:
        a, b = grid._to_ll.transform(x0, y), grid._to_ll.transform(x1, y)
        roads.append({"coords": [list(a), list(b)], "hw": "secondary" if k % 6 == 0 else "residential",
                      "name": None})
        y += step
        k += 1
    return {"pois": pois, "roads": roads, "places": [], "mock": True,
            "bbox": [south, west, north, east]}
