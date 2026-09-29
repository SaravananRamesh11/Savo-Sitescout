from shapely.geometry import box

from app.services import grid


def test_cell_id_roundtrip():
    assert grid.parse_cell_id(grid.make_cell_id(163, 25890)) == (163, 25890)


def test_cell_is_500m_square_in_utm():
    assert round(grid.cell_box_utm(10, 20).area) == 250_000


def test_cells_for_polygon_exact_multiple():
    cells = grid.cells_for_polygon(box(0, 0, 1000, 1000))
    assert {(c, r) for c, r, _ in cells} == {(0, 0), (1, 0), (0, 1), (1, 1)}
    assert all(abs(cov - 1.0) < 1e-9 for *_, cov in cells)


def test_non_overlapping_partition():
    poly = box(120, 80, 1730, 1210)
    cells = grid.cells_for_polygon(poly, min_coverage=0.0)
    total = sum(cov * 250_000 for *_, cov in cells)
    assert abs(total - poly.area) < 1  # cells partition the polygon exactly


def test_contiguity():
    assert grid.is_contiguous([(0, 0), (1, 0), (1, 1)])
    assert not grid.is_contiguous([(0, 0), (2, 0)])
    assert not grid.is_contiguous([(0, 0), (1, 1)])  # diagonal only is not edge-adjacent


def test_chennai_area_km2():
    a = grid.area_km2(box(80.22, 12.98, 80.2291, 12.989))  # ~1 km x 1 km near Velachery
    assert 0.9 < a < 1.1
