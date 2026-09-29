import pytest

from app.services import catchment_insights as I, survey_schema as S

X, Y = 80.2278, 12.9863


def cap(t, data, dx=0.0, dy=0.0):
    return {"type": t, "data": S.validate_capture(t, data), "lat": Y + dy, "lon": X + dx}


UNITS_DONE = [{"target": 10, "completed": 10}, {"target": 10, "completed": 9}]


def rich():
    caps = [
        cap("residential", {"independent_houses": 12, "apartment_complexes": 2, "activity_level": "medium"}),
        cap("residential", {"independent_houses": 8, "activity_level": "high", "occupancy": "high"}),
        cap("commercial", {"business_kind": "grocery", "count": 3, "activity_level": "high"}),
        cap("commercial", {"business_kind": "pharmacy", "activity_level": "medium"}),
        cap("traffic", {"pedestrian": "high", "vehicle": "medium", "observation_period": "evening"}),
        cap("traffic", {"pedestrian": "high", "vehicle": "high", "observation_period": "morning"}),
        cap("accessibility", {"road_condition": "good", "entry_exit": "easy", "parking": "moderate"}),
        cap("accessibility", {"road_condition": "fair", "entry_exit": "moderate", "parking": "easy", "obstruction": True}),
        cap("competition", {"name": "Big Mart", "competitor_type": "supermarket", "size": "large", "customer_activity": "high"}, 0.0008),
        cap("competition", {"name": "Big Mart", "competitor_type": "supermarket", "size": "large", "customer_activity": "high"}, 0.00081),  # same store
        cap("competition", {"name": "Raja Stores", "competitor_type": "kirana", "size": "small", "customer_activity": "medium"}, 0.003),
        cap("demand_generator", {"kind": "school", "name": "St. Mary"}),
        cap("demand_generator", {"kind": "hospital"}),
        cap("local_condition", {"condition": "waterlogging", "severity": "high"}),
    ]
    return caps


def test_aggregation_uses_only_recorded_data():
    out = I.aggregate(rich(), UNITS_DONE, prop_point=(X, Y))
    assert out["coverage_percentage"] == 95.0
    assert out["residential_summary"]["independent_houses"] == 20  # 12 + 8, exactly what was recorded
    assert out["residential_summary"]["apartment_complexes"] == 2
    assert out["commercial_summary"]["businesses_total"] == 4  # 3 groceries + 1 pharmacy
    assert out["traffic_summary"]["pedestrian"] == {"high": 2}
    assert out["traffic_summary"]["note"].startswith("Observed levels")  # no exact counts claimed
    assert out["accessibility_summary"]["obstructions"] == 1
    assert out["demand_generator_summary"]["by_kind"] == {"school": 1, "hospital": 1}
    assert out["overall_ground_fit_score"] is not None and 0 <= out["overall_ground_fit_score"] <= 100


def test_competitors_are_deduplicated_and_distance_measured():
    out = I.aggregate(rich(), UNITS_DONE, prop_point=(X, Y))
    comp = out["competition_summary"]
    assert comp["observations"] == 3 and comp["competitors"] == 2  # the two Big Mart pins are one competitor
    assert comp["nearest_to_property_m"] is not None and 60 < comp["nearest_to_property_m"] < 110
    assert comp["list"][0]["name"] == "Big Mart"


def test_findings_and_risks_quote_computed_values_only():
    out = I.aggregate(rich(), UNITS_DONE, prop_point=(X, Y))
    text = " ".join(out["key_findings"])
    assert "95%" in text and "2 competitor(s)" in text and "20 independent houses" in text
    codes = {r["code"] for r in out["risks"]}
    assert "severe_local_condition" in codes and "road_obstruction" in codes


def test_insufficient_data_is_reported_never_guessed():
    caps = [cap("residential", {"activity_level": "low"})]  # one observation only
    out = I.aggregate(caps, [{"target": 20, "completed": 1}], prop_point=None)
    assert out["residential_summary"]["sufficient"] is False
    assert out["residential_summary"]["status"] == "Insufficient data"
    assert out["overall_ground_fit_score"] is None  # fewer than three categories have enough data
    assert "insufficient_data" in out["data_quality_flags"] and "low_survey_coverage" in out["data_quality_flags"]
    assert out["competition_summary"]["sufficient"] is False  # "no competitors" would prove nothing at 5% coverage
    assert out["competition_summary"]["status"].startswith("Insufficient data")
    assert not any("competitor(s) recorded" in f for f in out["key_findings"])


def test_empty_survey_has_zero_coverage_and_no_invented_summary():
    out = I.aggregate([], [{"target": 10, "completed": 0}], None)
    assert out["coverage_percentage"] == 0.0 and out["overall_ground_fit_score"] is None
    assert out["residential_summary"] == {"observations": 0, "sufficient": False, "status": "Insufficient data"}


def test_coverage_is_capped_per_unit():
    assert I.coverage([{"target": 5, "completed": 50}, {"target": 5, "completed": 0}]) == 50.0
    assert I.coverage([]) == 0.0


def test_capture_validation_rejects_bad_input():
    with pytest.raises(ValueError):
        S.validate_capture("traffic", {"pedestrian": "busy", "vehicle": "low", "observation_period": "morning"})
    with pytest.raises(ValueError):
        S.validate_capture("traffic", {"pedestrian": "high", "vehicle": "low", "observation_period": "morning", "count": 500})  # no exact counts
    with pytest.raises(ValueError):
        S.validate_capture("competition", {"competitor_type": "supermarket", "size": "large"})  # customer_activity missing
    with pytest.raises(ValueError):
        S.validate_capture("nonsense", {})
    ok = S.validate_capture("commercial", {"business_kind": "grocery", "activity_level": "low"})
    assert ok["count"] == 1
