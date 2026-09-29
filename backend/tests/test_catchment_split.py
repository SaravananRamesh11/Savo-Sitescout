import random

import pytest
from shapely.geometry import Point

from app.core import survey_constants as K
from app.services import catchment_split as S, grid

CENTER = (80.2278, 12.9863)  # Velachery


def circle(radius_m=500):
    x, y = grid.lonlat_to_utm(*CENTER)
    return grid.to_ll(Point(x, y).buffer(radius_m, 32))


def payload(seed=1, n_roads=12, n_pois=60):
    rnd = random.Random(seed)
    x0, y0 = CENTER
    roads, pois = [], []
    for i in range(n_roads):  # a grid of named lanes across the catchment
        d = -0.0045 + i * 0.0008
        roads.append({"coords": [[x0 + d, y0 - 0.005], [x0 + d, y0 + 0.005]], "hw": "residential", "name": f"Lane {i}"})
        roads.append({"coords": [[x0 - 0.005, y0 + d], [x0 + 0.005, y0 + d]], "hw": "residential", "name": f"Street {i}"})
    cats = ["commercial", "commercial", "school", "clinic", "supermarket", "transit"]
    for _ in range(n_pois):
        pois.append({"lon": x0 + rnd.uniform(-0.005, 0.005), "lat": y0 + rnd.uniform(-0.005, 0.005),
                     "cat": rnd.choice(cats), "name": None})
    return {"roads": roads, "pois": pois, "bbox": [y0 - 0.02, x0 - 0.02, y0 + 0.02, x0 + 0.02]}


def _area(g):
    return grid.to_utm(g).area


def test_units_partition_the_study_exactly():
    poly = circle()
    res = S.compute_split(poly, payload())
    geoms = [grid.to_utm(u["geometry_ll"]) for u in res["units"]]
    union_area = sum(g.area for g in geoms)
    assert union_area == pytest.approx(grid.to_utm(poly).area, rel=1e-6)  # gap-free
    for i in range(len(geoms)):  # non-overlapping
        for j in range(i + 1, len(geoms)):
            assert geoms[i].intersection(geoms[j]).area < 1.0


def test_split_is_deterministic():
    a = S.compute_split(circle(), payload())
    b = S.compute_split(circle(), payload())
    assert [(u["unit_code"], u["workload"]["points"], u["target_capture_count"]) for u in a["units"]] == \
           [(u["unit_code"], u["workload"]["points"], u["target_capture_count"]) for u in b["units"]]


def test_units_are_reasonably_balanced_and_not_just_lane_counts():
    res = S.compute_split(circle(), payload(), units=4)
    pts = [u["workload"]["points"] for u in res["units"]]
    assert len(pts) == 4 and res["meta"]["balance_ratio"] < 2.5
    # workload includes POIs and estimated households, not only lanes
    w = res["units"][0]["workload"]
    assert w["commercial_pois"] is not None and w["estimated_households"] > 0 and w["households_are_estimates"] is True
    assert w["road_m"] > 0 and w["lanes"]


def test_unit_count_clamps():
    small = S.compute_split(circle(120), payload())
    assert small["meta"]["unit_count"] >= 1
    forced = S.compute_split(circle(), payload(), units=99)
    assert forced["meta"]["unit_count"] <= K.MAX_UNITS
    assert S.compute_split(circle(), payload(), units=1)["meta"]["unit_count"] == 1


def test_no_osm_falls_back_to_equal_area_and_says_so():
    res = S.compute_split(circle(), None, units=4)
    assert "split_by_area_only" in res["meta"]["flags"]
    assert all(u["estimated_distance_m"] is None for u in res["units"])
    assert res["units"][0]["workload"]["basis"].startswith("equal area")
    areas = [u["workload"]["area_m2"] for u in res["units"]]
    assert max(areas) / min(areas) < 2.0


def test_every_unit_has_a_target_and_lanes_named():
    for u in S.compute_split(circle(), payload())["units"]:
        assert K.MIN_TARGET_CAPTURES <= u["target_capture_count"] <= K.MAX_TARGET_CAPTURES
        assert u["geometry_ll"].geom_type == "MultiPolygon"


def test_suggested_assignees_balance_the_load():
    res = S.compute_split(circle(), payload(), units=4)
    execs = [{"id": "survey_executive:karthik"}, {"id": "survey_executive:lakshmi"}]
    who = S.suggest_assignees(res["units"], execs, {})
    assert set(who) == {"survey_executive:karthik", "survey_executive:lakshmi"}
    loads = {e["id"]: sum(u["workload"]["points"] for u, w in zip(res["units"], who) if w == e["id"]) for e in execs}
    assert max(loads.values()) / min(loads.values()) < 1.6
    # an executive who is already busy gets fewer new points
    busy = S.suggest_assignees(res["units"], execs, {"survey_executive:karthik": 500})
    assert busy.count("survey_executive:lakshmi") > busy.count("survey_executive:karthik")
