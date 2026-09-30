"""Opportunity Finder maths that needs no database or network: score composition, scouting coverage, tiles, eligibility,
ranking. Same style as test_scoring.py / test_grid.py."""
import pytest

from app.core import opportunity_constants as C
from app.services import grid, opportunity_coverage as cov, opportunity_finder as finder, opportunity_tiles as tiles, scoring
from app.services.opportunity_score import score_cell

FEATS = {"pop_density": 20000, "household_density": 5000, "growth_pct": 2.0, "road_km_per_km2": 12.0,
         "major_road_dist_m": 400.0, "transit_count": 2, "commercial_per_km2": 200.0, "amenity_units_per_km2": 10.0,
         "competitor_count": 5, "savomart_dist_m": 1800.0}


# ------------------------------------------------------------------ score
def test_w_zero_is_exactly_the_m1_score_and_w_one_is_pure_unscouted():
    m1 = scoring.score_features(FEATS)["total"]
    assert score_cell(FEATS, 0.3, w=0.0)["total"] == pytest.approx(m1, abs=0.01)
    assert score_cell(FEATS, 0.25, w=1.0)["total"] == pytest.approx(75.0)
    assert score_cell(FEATS, 0.0, w=1.0)["total"] == 100.0


def test_composition_formula_and_breakdown_adds_up():
    w = 0.2
    r = score_cell(FEATS, 0.5, w=w)
    m1 = scoring.score_features(FEATS)["total"]
    assert r["total"] == pytest.approx((1 - w) * m1 + w * 100 * 0.5, abs=0.01)
    assert r["m1_score"] == pytest.approx(m1, abs=0.01)
    assert sum(b["points"] for b in r["breakdown"]) == pytest.approx(r["total"], abs=0.05)
    assert sum(b["weight"] for b in r["breakdown"]) == pytest.approx(100.0, abs=0.05)  # weights still sum to 100
    assert r["breakdown"][-1]["key"] == "unscouted_opportunity" and len(r["breakdown"]) == 11
    assert {b["key"] for b in r["breakdown"][:-1]} == {b["key"] for b in scoring.score_features(FEATS)["breakdown"]}


def test_less_scouting_never_lowers_the_score_and_it_is_deterministic():
    scores = [score_cell(FEATS, c / 10)["total"] for c in range(0, 11)]
    assert scores == sorted(scores, reverse=True)
    assert score_cell(FEATS, 0.4) == score_cell(FEATS, 0.4)
    assert score_cell(FEATS, 5.0)["coverage"] == 1.0 and score_cell(FEATS, -1)["coverage"] == 0.0  # clamped


def test_m1_factor_weights_are_untouched():
    from app.core import scoring_constants as K

    assert sum(K.WEIGHTS.values()) == 100 and len(K.WEIGHTS) == 10


# ------------------------------------------------------------------ scouting coverage
def _sig(cell, units, age, spread=True, kind="property"):
    return {"cells": [cell] if isinstance(cell, tuple) else cell, "units": units, "age_days": age, "spread": spread,
            "kind": kind, "ref": 1}


def test_recency_fades_from_full_to_zero():
    assert cov.recency(0) == 1.0 and cov.recency(C.RECENT_FULL_DAYS) == 1.0
    mid = cov.recency((C.RECENT_FULL_DAYS + C.EXPIRE_DAYS) / 2)
    assert mid == pytest.approx(0.5)
    assert cov.recency(C.EXPIRE_DAYS) == 0.0 and cov.recency(9999) == 0.0


def test_a_lone_property_lowers_priority_a_little_and_does_not_exclude_the_cell():
    r = cov.combine([_sig((10, 10), 1.0, 5)])
    own = r[grid.make_cell_id(10, 10)]["coverage"]
    assert own == pytest.approx(1.0 / C.COVERAGE_FULL_UNITS, abs=0.001)
    assert own < 0.5  # still mostly unscouted
    assert score_cell(FEATS, own)["total"] > score_cell(FEATS, 1.0)["total"]


def test_neighbours_get_half_credit_and_far_cells_none():
    r = cov.combine([_sig((10, 10), 1.0, 5)])
    nb = r[grid.make_cell_id(11, 11)]
    assert nb["units"] == pytest.approx(C.NEIGHBOUR_SHARE) and nb["signals"][0]["share"] == C.NEIGHBOUR_SHARE
    assert grid.make_cell_id(12, 10) not in r and len(r) == 9


def test_units_add_up_and_are_capped_at_one():
    r = cov.combine([_sig((5, 5), 1.0, 1), _sig((5, 5), 1.0, 1), _sig((5, 5), 1.0, 1), _sig((5, 5), 1.0, 1)])
    assert r[grid.make_cell_id(5, 5)]["coverage"] == 1.0


def test_old_signals_count_less_and_expired_ones_not_at_all():
    fresh = cov.combine([_sig((1, 1), 3.0, 10)])[grid.make_cell_id(1, 1)]["coverage"]
    old = cov.combine([_sig((1, 1), 3.0, 250)])[grid.make_cell_id(1, 1)]["coverage"]
    assert fresh == 1.0 and 0 < old < fresh
    assert cov.combine([_sig((1, 1), 3.0, C.EXPIRE_DAYS + 1)]) == {}


def test_completed_catchment_study_fills_every_covered_cell_without_spreading():
    cells = [(20, 20), (21, 20), (20, 21)]
    r = cov.combine([_sig(cells, C.UNITS_STUDY_COMPLETED, 3, spread=False, kind="catchment_study")])
    assert {k: v["coverage"] for k, v in r.items()} == {grid.make_cell_id(*c): 1.0 for c in cells}
    active = cov.combine([_sig(cells, C.UNITS_STUDY_ACTIVE, 3, spread=False, kind="catchment_study_active")])
    assert all(v["coverage"] == pytest.approx(0.5) for v in active.values())


def test_describe_is_readable():
    assert "No recent" in cov.describe(None)
    text = cov.describe(cov.combine([_sig((1, 1), 1.0, 3)])[grid.make_cell_id(1, 1)])
    assert "33% scouted" in text and "property" in text


# ------------------------------------------------------------------ tiles
def test_tiles_are_lattice_aligned_disjoint_and_cover_the_bbox_exactly():
    bbox = (80.20, 12.95, 80.27, 13.03)
    t = tiles.city_tiles(bbox)
    all_cells = [c for cells in t.values() for c in cells]
    assert len(all_cells) == len(set(all_cells)) == len(grid.cells_in_bbox_ll(*bbox))  # no gaps, no overlaps
    for key, cells in t.items():
        tc, tr = map(int, key.split(":")[1].split("_"))
        assert all(c // C.TILE_CELLS == tc and r // C.TILE_CELLS == tr for c, r in cells)
        assert len(cells) <= C.TILE_CELLS ** 2
    n = C.TILE_CELLS
    assert tiles.tile_of_cell(0, 0) == f"{n}:0_0" and tiles.tile_of_cell(n, n - 1) == f"{n}:1_0"
    # the cache always holds the whole tile, so a tile cached under one bbox is complete for another
    for key, cells in t.items():
        full = tiles.full_tile_cells(key)
        assert len(full) == n * n and set(cells) <= set(full)


def test_the_default_city_grid_is_the_m1_lattice_and_a_sane_size():
    t = tiles.city_tiles()
    n = sum(len(v) for v in t.values())
    assert 2000 < n < 12000 and 10 < len(t) < 120  # a city, not the whole state; each tile is one Overpass call


def test_nearest_store_distance_uses_cell_centres():
    stores = [{"name": "A", "longitude": 80.22, "latitude": 12.98}, {"name": "B", "longitude": 80.30, "latitude": 13.05}]
    x, y = grid.lonlat_to_utm(80.22, 12.98)
    cell = grid.cell_of_utm(x, y)
    (d, name), = tiles.nearest_stores([cell], stores).values()
    assert name == "A" and d < 400
    assert tiles.nearest_stores([cell], []) == {}


def test_tile_features_come_from_the_m1_code_and_drop_nothing_needed():
    cells = [(c, r) for c in range(10, 12) for r in range(10, 12)]
    x, y = (10.5 * 500, 10.5 * 500)
    lon, lat = grid._to_ll.transform(x, y)
    osm = {"pois": [{"lon": lon, "lat": lat, "cat": "school", "name": "S"}], "roads": [], "places": [{"name": "P", "lon": lon, "lat": lat, "place": "suburb"}]}
    payload = tiles.compute_tile(cells, osm)
    assert set(payload["cells"]) == {grid.make_cell_id(c, r) for c, r in cells}
    one = payload["cells"][grid.make_cell_id(10, 10)]
    assert one["counts"] == {"school": 1} and one["evidence"] is True
    assert payload["cells"][grid.make_cell_id(11, 11)]["evidence"] is False
    assert "savomart_dist_m" not in one  # stores change; added fresh on every run
    assert payload["places"][0]["name"] == "P" and payload["counts"]["pois"] == 1


# ------------------------------------------------------------------ eligibility and ranking
def test_eligibility_never_lets_an_estimate_stand_in_for_missing_land():
    assert finder.eligibility(None) == "tile_missing"
    assert finder.eligibility({"evidence": False, "demo_dist_km": 1}) == "no_mapped_features"
    assert finder.eligibility({"evidence": True, "demo_dist_km": C.MAX_DEMO_DISTANCE_KM + 0.1}) == "outside_demographic_table"
    assert finder.eligibility({"evidence": True, "demo_dist_km": 1.0}) is None


def test_top_cells_are_spread_and_ordered_by_score():
    scores = {grid.make_cell_id(c, r): {"col": c, "row": r, "score": s} for c, r, s in
              [(0, 0, 90), (1, 0, 89), (5, 5, 80), (9, 9, 70), (0, 1, 88)]}
    top = finder.rank_cells(scores, 3)
    assert top == [grid.make_cell_id(0, 0), grid.make_cell_id(5, 5), grid.make_cell_id(9, 9)]  # neighbours of #1 skipped


def test_cell_corners_match_the_m1_cell_polygon():
    col, row = grid.cell_of_utm(*grid.lonlat_to_utm(80.2250, 12.9850))  # a real Velachery cell
    corners = finder.cell_corners_ll([(col, row)])[0]
    poly = grid.cell_polygon_ll(col, row)
    expected = sorted((round(x, 5), round(y, 5)) for x, y in list(poly.exterior.coords)[:4])  # same four corners
    assert sorted((round(lo, 5), round(la, 5)) for lo, la in corners) == expected


def test_risks_include_the_population_estimate_warning_and_weak_factors():
    breakdown = scoring.score_features({**FEATS, "competitor_count": 15})["breakdown"]
    risks = finder.risks_for(FEATS, breakdown, {"status": "stale", "fetched_at": "2026-09-01T00:00:00+00:00"})
    assert any("ESTIMATE" in r for r in risks) and any("older cache" in r for r in risks)
    assert any(r.startswith("Weak competition") for r in risks)


def test_ranking_is_numbered_strictly_by_score_even_when_fillers_are_needed():
    # only two spread-out cells exist, so the touching cell is a "filler" that scores higher than the second spread pick
    scores = {grid.make_cell_id(c, r): {"col": c, "row": r, "score": s} for c, r, s in [(0, 0, 90), (1, 0, 88), (9, 9, 70)]}
    top = finder.rank_cells(scores, 3)
    assert [scores[c]["score"] for c in top] == [90, 88, 70]
