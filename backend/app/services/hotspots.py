"""Pick top-N scouting hotspots and explain them with deterministic, number-backed bullets."""
from shapely.geometry import Point

from app.core.config import HOTSPOT_COUNT
from app.services import grid


def pick_hotspots(cell_scores: dict[str, dict], n: int = HOTSPOT_COUNT) -> list[str]:
    """Top-N cells by score; prefers spatially spread cells (no touching neighbours) and cells mostly
    inside the area, then fills remaining slots by score."""
    ranked = sorted(cell_scores.values(), key=lambda c: c["score"], reverse=True)
    eligible = [c for c in ranked if c["coverage"] >= 0.4] or ranked
    chosen: list[dict] = []
    for c in eligible:
        if all(max(abs(c["col"] - o["col"]), abs(c["row"] - o["row"])) > 1 for o in chosen):
            chosen.append(c)
        if len(chosen) == n:
            break
    for c in eligible:
        if len(chosen) == n:
            break
        if c not in chosen:
            chosen.append(c)
    return [c["cell_id"] for c in chosen]


def nearest_locality(lat: float, lon: float, places: list[dict], fallback: str, max_m: float = 2500) -> str:
    if not places:
        return fallback
    x, y = grid.lonlat_to_utm(lon, lat)
    p = Point(x, y)
    best, bd = None, None
    for pl in places:
        px, py = grid.lonlat_to_utm(pl["lon"], pl["lat"])
        d = p.distance(Point(px, py))
        if bd is None or d < bd:
            best, bd = pl, d
    return best["name"] if best and bd <= max_m else fallback


def why_bullets(cell_breakdown: list[dict], area_breakdown: list[dict], k: int = 3) -> list[str]:
    """Strongest factors relative to the area average, phrased from the raw numbers."""
    area_pts = {f["key"]: f["points"] for f in area_breakdown}
    deltas = sorted(cell_breakdown, key=lambda f: f["points"] - area_pts.get(f["key"], 0), reverse=True)
    out = []
    for f in deltas[:k]:
        diff = f["points"] - area_pts.get(f["key"], 0)
        if diff <= 0.05 and out:
            break
        out.append(f"{f['label']}: {f['explanation']} ({f['points']:.1f}/{f['weight']} pts, "
                   f"area avg {area_pts.get(f['key'], 0):.1f})")
    return out
