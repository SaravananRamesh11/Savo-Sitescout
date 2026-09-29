import json

from app.services import grounding, llm, report_text

FACTS = {
    "area": {"name": "Velachery", "area_km2": 5.58},
    "constants": {"cell_size_m": 500, "competition_radius_m": 1000, "transit_radius_m": 500, "max_score": 100},
    "overall": {"score": 60.3, "rating": "Good fit"},
    "factors": [
        {"key": "road_density", "label": "Road density", "group": "Accessibility", "weight": 8, "points": 8.0,
         "raw": 25.1, "unit": "km/km²", "explanation": "25.1 km of road per km² vs 20 for full points"},
        {"key": "commercial_activity", "label": "Commercial activity", "group": "Commercial activity", "weight": 10,
         "points": 1.3, "raw": 67.0, "unit": "POIs/km²", "explanation": "67 shops per km² vs 500 for full points"},
    ],
    "profile": {"schools": 15, "estimated_population": 109200},
    "hotspots": [{"rank": 1, "cell_id": "830_2871", "score": 65.2, "locality": "Rajalakshmi Nagar",
                  "nearest_named_road": None, "why": ["Amenity activity 30.0"]}],
    "data_quality_flags": ["population_mocked"],
}


def test_grounded_text_passes():
    assert grounding.find_ungrounded(["Velachery scores 60.3 (Good fit) with 15 schools and 109,200 people"], FACTS) == []


def test_invented_number_is_caught():
    bad = grounding.find_ungrounded(["There are 42 schools in the area"], FACTS)
    assert bad == ["42"]


def test_metres_to_km_conversion_allowed_only_for_known_values():
    facts = {"d": 4170}
    assert grounding.find_ungrounded(["4.2 km away"], facts) == []
    assert grounding.find_ungrounded(["9.9 km away"], facts) == ["9.9"]


def test_template_is_built_only_from_facts():
    out = report_text.template_explanation(FACTS)
    assert grounding.find_ungrounded([out["summary"]] + [r["text"] for r in out["reasons"]], FACTS) == []


def test_llm_with_invented_numbers_is_rejected_and_template_used(monkeypatch):
    monkeypatch.setattr(llm, "is_configured", lambda: True)
    bad = json.dumps({"summary": "Velachery is great with 99 schools.", "reasons": [], "scout_first": [], "caveats": []})
    monkeypatch.setattr(llm, "complete", lambda *a, **k: bad)
    out, source, notes = report_text.generate_explanation(FACTS)
    assert source == "template"
    assert any("rejected" in n for n in notes)


def test_llm_with_grounded_output_is_accepted(monkeypatch):
    monkeypatch.setattr(llm, "is_configured", lambda: True)
    good = json.dumps({
        "summary": "Velachery scores 60.3/100 (Good fit).",
        "reasons": [{"factor": "road_density", "text": "25.1 km of road per km²."}],
        "scout_first": [{"cell_id": "830_2871", "text": "Rajalakshmi Nagar scores 65.2."}],
        "caveats": ["Population figures are estimates."]})
    monkeypatch.setattr(llm, "complete", lambda *a, **k: good)
    out, source, _ = report_text.generate_explanation(FACTS)
    assert source == "llm" and out["summary"].startswith("Velachery")


def test_llm_unknown_factor_key_is_rejected(monkeypatch):
    monkeypatch.setattr(llm, "is_configured", lambda: True)
    bad = json.dumps({"summary": "Velachery scores 60.3.", "reasons": [{"factor": "made_up", "text": "x"}],
                      "scout_first": [], "caveats": []})
    monkeypatch.setattr(llm, "complete", lambda *a, **k: bad)
    assert report_text.generate_explanation(FACTS)[1] == "template"
