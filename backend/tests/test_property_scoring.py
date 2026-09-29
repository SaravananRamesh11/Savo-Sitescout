import pytest

from app.core import property_scoring_constants as K
from app.services import grounding, property_scoring as S, property_text

POI = {"available": True, "counts": {"school": {250: 1, 500: 3}, "college": {250: 0, 500: 1},
                                     "hospital": {250: 0, 500: 1}},
       "organised": {500: 0, 1000: 0}, "other_grocery": {500: 2, 1000: 4}, "nearest_organised_m": None}

BASE = {
    "monthly_rent": 100000, "total_area_sqft": 2000, "sales_area_sqft": 1400, "storage_area_sqft": 400,
    "frontage_ft": 20, "road_width_ft": 40, "visibility_score": 5, "entry_access": "easy", "exit_access": "easy",
    "is_main_road_frontage": True, "is_corner_property": True, "traffic_signal_nearby": False,
    "two_wheeler_parking": True, "four_wheeler_parking": True, "parking_capacity": 6, "parking_type": "on_property",
    "poi": POI, "field_organised_competitors": 0,
    "demo": {"household_density": 7500, "pop_density": 30000, "growth_pct": 2, "mocked": False},
    "m1": {"area_score": 70, "cell_score": 70, "is_hotspot": True, "report_flags": []},
}


def pts(res):
    return {f["key"]: f for f in res["breakdown"]}


def test_all_factors_scored_and_weights_sum_to_100():
    res = S.score_property({**BASE, "expected_monthly_revenue": 2_500_000})  # rent = 4% of revenue -> in band
    assert res["unscored"] == []
    assert sum(f["effective_weight"] for f in res["breakdown"]) == pytest.approx(100, abs=0.1)
    assert 0 <= res["total"] <= 100 and res["confidence"] == 1.0  # nothing missing and nothing estimated


def test_rent_without_revenue_is_insufficient_data_not_zero():
    res = S.score_property(BASE)
    f = pts(res)["rent_affordability"]
    assert f["scored"] is False and f["points"] is None and "Insufficient data" in f["explanation"]
    assert "rent_affordability" in res["unscored"]
    assert res["metrics"]["rent_per_sqft"] == 50.0 and res["metrics"]["rent_to_revenue"] is None
    # remaining factors are renormalised to 100, and confidence reflects the missing weight
    assert sum(x["effective_weight"] for x in res["breakdown"]) == pytest.approx(100, abs=0.1)
    assert res["confidence"] == pytest.approx(0.85, abs=0.001)


def test_rent_ratio_band():
    within = pts(S.score_property({**BASE, "expected_monthly_revenue": 2_500_000}))["rent_affordability"]
    over = pts(S.score_property({**BASE, "expected_monthly_revenue": 1_000_000}))["rent_affordability"]  # 10%
    assert within["norm"] == 1.0 and over["norm"] == 0.0
    assert within["raw"] == 0.04


def test_traffic_signal_is_a_modifier_not_a_bonus():
    plain = pts(S.score_property(BASE))["accessibility"]["norm"]
    good = pts(S.score_property({**BASE, "traffic_signal_nearby": True}))["accessibility"]["norm"]
    bad = pts(S.score_property({**BASE, "traffic_signal_nearby": True, "entry_access": "difficult"}))["accessibility"]["norm"]
    no_signal_difficult = pts(S.score_property({**BASE, "entry_access": "difficult"}))["accessibility"]["norm"]
    assert good >= plain  # easy access + signal: small positive (already near the cap)
    assert bad < no_signal_difficult  # signal + difficult entry is worse than difficult alone


def test_zero_competition_depends_on_demand():
    weak = {**POI, "counts": {c: {250: 0, 500: 0} for c in POI["counts"]}}
    strong = POI
    a = pts(S.score_property({**BASE, "poi": weak}))["competition"]["norm"]
    b = pts(S.score_property({**BASE, "poi": strong}))["competition"]["norm"]
    assert a == pytest.approx(0.5, abs=0.01)  # zero competition + no demand is neutral, not "high"
    assert b > 0.8


def test_organised_competition_lowers_score_and_sources_are_not_added():
    many = {**POI, "organised": {500: 2, 1000: 5}}
    few = {**POI, "organised": {500: 0, 1000: 1}}
    assert pts(S.score_property({**BASE, "poi": many}))["competition"]["norm"] < \
        pts(S.score_property({**BASE, "poi": few}))["competition"]["norm"]
    both = pts(S.score_property({**BASE, "poi": {**POI, "organised": {500: 0, 1000: 2}}, "field_organised_competitors": 2}))
    assert both["competition"]["raw"] == 2  # max of the two sources, never the sum


def test_no_osm_means_demand_and_competition_unscored():
    res = S.score_property({**BASE, "poi": {"available": False}})
    assert {"demand_generators", "competition"} <= set(res["unscored"])


def test_space_ratios_only_when_measured():
    m = S.derived_metrics({**BASE, "sales_area_sqft": None, "storage_area_sqft": None})
    assert m["sales_ratio"] is None and m["storage_ratio"] is None and m["storage_to_sales"] is None
    m2 = S.derived_metrics(BASE)
    assert m2["sales_ratio"] == 0.7 and m2["storage_ratio"] == 0.2 and m2["storage_to_sales"] == pytest.approx(0.286, abs=0.001)
    only_total = pts(S.score_property({**BASE, "sales_area_sqft": None, "storage_area_sqft": None}))["space_utilisation"]
    assert "Sales/storage split was not measured" in only_total["explanation"]


def test_mocked_demographics_lower_confidence():
    real = S.score_property({**BASE, "demo": {**BASE["demo"], "mocked": False}})
    mock = S.score_property({**BASE, "demo": {**BASE["demo"], "mocked": True}})
    assert mock["confidence"] < real["confidence"]
    assert pts(mock)["demographic_fit"]["estimated_input"] is True


def test_nothing_scoreable_returns_none_not_zero():
    res = S.score_property({"poi": {"available": False}})
    assert res["total"] is None and res["confidence"] == 0.0


def test_risks_and_recommendation():
    res = S.score_property({**BASE, "m1": {**BASE["m1"], "nearest_savomart_m": 200, "nearest_savomart": "Palavakkam",
                                            "area_report_id": 1}})
    risks = S.build_risks({**BASE, "m1": {"nearest_savomart_m": 200, "nearest_savomart": "Palavakkam",
                                          "area_report_id": 1, "report_flags": []}}, res, [], {})
    assert any(r["code"] == "cannibalisation" and r["severity"] == "high" for r in risks)
    rec, _ = S.recommend(res["total"], res["confidence"], risks)
    assert rec == "REVIEW"  # good score but a hard-stop risk
    assert S.recommend(80, 0.9, [])[0] == "PROCEED_TO_CATCHMENT"
    assert S.recommend(30, 0.9, [])[0] == "NOT_RECOMMENDED"
    assert S.recommend(None, 0.0, [])[0] == "REVIEW"


def test_property_explanation_rejects_invented_numbers(monkeypatch):
    from app.services import llm
    import json

    facts = property_text.build_facts(1, BASE, S.score_property(BASE), S.score_property(BASE)["metrics"], [], "REVIEW", [])
    monkeypatch.setattr(llm, "is_configured", lambda: True)
    bad = json.dumps({"summary": "This property will earn Rs 99 crore a year.", "reasons": [], "risk_notes": [], "caveats": []})
    monkeypatch.setattr(llm, "complete", lambda *a, **k: bad)
    out, source, notes = property_text.generate_explanation(facts)
    assert source == "template" and any("rejected" in n for n in notes)
    assert grounding.find_ungrounded([out["summary"]], facts) == []


def test_weights_are_configurable_constants():
    assert set(K.WEIGHTS) == set(S.FACTORS)
    assert round(sum(K.WEIGHTS.values())) == 100
