from app.services import features, grid, hotspots
from app.services.scoring import score_features


def _cs(col, row, score, cov=1.0):
    return {"cell_id": f"{col}_{row}", "col": col, "row": row, "score": score, "coverage": cov}


def test_hotspots_prefer_spread_cells():
    cells = {c["cell_id"]: c for c in [_cs(0, 0, 90), _cs(1, 0, 89), _cs(5, 5, 70), _cs(9, 9, 60), _cs(0, 5, 50),
                                        _cs(20, 20, 40)]}
    ids = hotspots.pick_hotspots(cells, 3)
    assert ids[0] == "0_0"
    assert "1_0" not in ids  # touching neighbour of #1 is skipped while spread options exist


def test_hotspots_skip_sliver_cells():
    cells = {c["cell_id"]: c for c in [_cs(0, 0, 99, cov=0.1), _cs(3, 3, 50), _cs(6, 6, 40)]}
    assert hotspots.pick_hotspots(cells, 2)[0] == "3_3"


def test_feature_binning_counts_each_poi_in_exactly_one_cell():
    from shapely.geometry import box

    x0, y0 = 80.2200, 12.9800
    poly_ll = grid.to_ll(grid.to_utm(box(x0, y0, x0 + 0.01, y0 + 0.01)))
    cells = grid.cells_for_polygon(grid.to_utm(poly_ll))
    pois = [{"lon": x0 + 0.002 + i * 0.001, "lat": y0 + 0.003, "cat": "commercial", "name": None} for i in range(6)]
    osm = {"pois": pois, "roads": [], "places": []}
    feats, totals = features.compute_cell_features(poly_ll, cells, osm, [])
    assert totals["commercial"] == 6
    assert sum(sum(f["counts"].values()) for f in feats.values()) == 6


def test_area_aggregate_uses_same_scoring_path():
    feats = {"a": {"coverage": 1.0, **{k: 10.0 for k in features.FEATURE_KEYS}},
             "b": {"coverage": 1.0, **{k: 30.0 for k in features.FEATURE_KEYS}}}
    agg = features.aggregate_area(feats)
    assert agg["pop_density"] == 20.0
    assert score_features(agg)["total"] > 0
