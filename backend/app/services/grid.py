"""500 m grid on a fixed UTM-44N lattice. All metric maths happens in UTM metres, never degrees."""
import math
from collections import deque

from pyproj import Transformer
from shapely.geometry import Polygon, box
from shapely.ops import transform, unary_union

from app.core.config import CELL_SIZE_M, UTM_EPSG

_to_utm = Transformer.from_crs(4326, UTM_EPSG, always_xy=True)
_to_ll = Transformer.from_crs(UTM_EPSG, 4326, always_xy=True)


def to_utm(geom):
    return transform(_to_utm.transform, geom)


def to_ll(geom):
    return transform(_to_ll.transform, geom)


def lonlat_to_utm(lon: float, lat: float) -> tuple[float, float]:
    return _to_utm.transform(lon, lat)


def make_cell_id(col: int, row: int) -> str:
    return f"{col}_{row}"


def parse_cell_id(cid: str) -> tuple[int, int]:
    try:
        col, row = cid.split("_")
        return int(col), int(row)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Invalid cell id '{cid}'") from exc


def cell_of_utm(x: float, y: float) -> tuple[int, int]:
    return math.floor(x / CELL_SIZE_M), math.floor(y / CELL_SIZE_M)


def cell_box_utm(col: int, row: int) -> Polygon:
    return box(col * CELL_SIZE_M, row * CELL_SIZE_M, (col + 1) * CELL_SIZE_M, (row + 1) * CELL_SIZE_M)


def cell_polygon_ll(col: int, row: int) -> Polygon:
    return to_ll(cell_box_utm(col, row))


def cells_for_polygon(poly_utm, min_coverage: float = 0.08) -> list[tuple[int, int, float]]:
    """Cells intersecting a UTM polygon with the share of each cell inside it (drops slivers)."""
    minx, miny, maxx, maxy = poly_utm.bounds
    c0, r0 = cell_of_utm(minx, miny)
    c1, r1 = cell_of_utm(maxx, maxy)
    cell_area = CELL_SIZE_M * CELL_SIZE_M
    out = []
    for col in range(c0, c1 + 1):
        for row in range(r0, r1 + 1):
            cb = cell_box_utm(col, row)
            if not cb.intersects(poly_utm):
                continue
            cov = cb.intersection(poly_utm).area / cell_area
            if cov >= min_coverage:
                out.append((col, row, min(cov, 1.0)))
    return out


def cells_in_bbox_ll(minlon, minlat, maxlon, maxlat, limit: int = 4000) -> list[tuple[int, int]]:
    x0, y0 = lonlat_to_utm(minlon, minlat)
    x1, y1 = lonlat_to_utm(maxlon, maxlat)
    c0, r0 = cell_of_utm(min(x0, x1), min(y0, y1))
    c1, r1 = cell_of_utm(max(x0, x1), max(y0, y1))
    n = (c1 - c0 + 1) * (r1 - r0 + 1)
    if n > limit:
        raise ValueError(f"Bounding box too large ({n} cells > {limit}); zoom in")
    return [(c, r) for c in range(c0, c1 + 1) for r in range(r0, r1 + 1)]


def is_contiguous(cells: list[tuple[int, int]]) -> bool:
    """Edge-adjacent connectivity (4-neighbour)."""
    s = set(cells)
    if not s:
        return False
    seen = {next(iter(s))}
    q = deque(seen)
    while q:
        c, r = q.popleft()
        for n in ((c + 1, r), (c - 1, r), (c, r + 1), (c, r - 1)):
            if n in s and n not in seen:
                seen.add(n)
                q.append(n)
    return len(seen) == len(s)


def union_of_cells_ll(cells: list[tuple[int, int]]) -> Polygon:
    u = unary_union([cell_box_utm(c, r) for c, r in cells])
    if u.geom_type != "Polygon":  # contiguity is validated upstream; keep the outer hull as a guard
        u = u.convex_hull
    return to_ll(u)


def area_km2(poly_ll) -> float:
    return to_utm(poly_ll).area / 1e6
