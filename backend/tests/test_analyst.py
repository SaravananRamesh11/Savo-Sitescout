"""Conversational analyst tests (real database for the tools; the LLM is scripted, so nothing leaves the machine).

Rows are created with a unique tag and removed afterwards; the existing data is only read.
"""
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from geoalchemy2.shape import from_shape
from shapely.geometry import Point, box
from sqlalchemy import text

from app.core import config
from app.core.personas import BY_ID
from app.services import analyst, analyst_tools as T, llm

from tests.test_property_integration import MGR  # noqa: E402

needs_db = pytest.mark.skipif(not config.DB_URL, reason="database url not configured")
PERSONA = BY_ID["bd_manager:asha"]


def _factors(pop, comp):
    return [{"key": "population_density", "label": "Population density", "weight": 20, "points": pop,
             "raw": pop * 1000, "unit": "people/km2"},
            {"key": "competition", "label": "Competition", "weight": 15, "points": comp, "raw": 3, "unit": "stores/km"}]


def _eval_factors(rent, access):
    return [{"key": "rent_affordability", "label": "Rent affordability", "weight": 15.0, "effective_weight": 15.0,
             "scored": True, "norm": 0.5, "points": rent, "raw": 5.0, "explanation": "rent"},
            {"key": "access", "label": "Access", "weight": 10.0, "effective_weight": 10.0, "scored": True, "norm": 0.5,
             "points": access, "raw": "easy", "explanation": "access"}]


@pytest.fixture()
def data():
    from app.core.db import get_session
    from app.models.db_models import Area, AreaReport
    from app.models.property_models import Property, PropertyEvaluation
    from app.models.survey_models import CatchmentInsight, CatchmentStudy

    db = get_session()
    tag = uuid.uuid4().hex[:8]
    now = datetime.now(timezone.utc)
    ns = type("NS", (), {})()
    ns.tag = tag
    areas = []
    for i, (label, score, pop, comp) in enumerate([("Alpha", 72.5, 18.0, 9.0), ("Beta", 64.0, 12.0, 11.0)]):
        a = Area(input_type="name", raw_input=f"analyst_{label}_{tag}", resolved_name=f"Zq{label}{tag}", area_km2=2.0,
                 geometry=from_shape(box(80.1 + i * 0.05, 12.9, 80.12 + i * 0.05, 12.92), srid=4326))
        db.add(a)
        db.flush()
        db.add(AreaReport(area_id=a.id, status="completed", steps=[], created_at=now, completed_at=now,
                          overall_score=score, rating="Good fit", score_breakdown=_factors(pop, comp),
                          area_profile={"hotspots": [{"rank": 1, "cell_id": f"1_{i}", "score": 70.0, "locality": label,
                                                      "why": ["a", "b", "c"]}]}, data_quality_flags=[]))
        areas.append(a)
    ns.area_a, ns.area_b = areas

    def prop(area, address, stage, score, rent, submitted=True, factors=None):
        p = Property(area_id=area.id, created_by="bd_executive:ravi", location=from_shape(Point(80.11, 12.91), srid=4326),
                     pipeline_stage=stage, address=address, locality="Testville", monthly_rent=rent,
                     total_area_sqft=1000.0, duplicate_flags=[], submitted_at=now if submitted else None)
        db.add(p)
        db.flush()
        if score is not None:
            db.add(PropertyEvaluation(property_id=p.id, evaluation_version=1, status="completed", overall_score=score,
                                      confidence=0.8, score_breakdown=factors or _eval_factors(8.0, 6.0),
                                      recommendation="PROCEED_TO_CATCHMENT", risks=["narrow road"], data_quality_flags=[],
                                      m1_context={"area_score": 72.5}, trigger="submitted", created_by="bd_executive:ravi"))
        return p

    ns.p1 = prop(ns.area_a, f"High Road {tag}", "UNDER_REVIEW", 78.4, 90000, factors=_eval_factors(12.0, 8.0))
    ns.p2 = prop(ns.area_a, f"Low Road {tag}", "SUBMITTED", 61.2, 120000, factors=_eval_factors(6.0, 8.0))
    ns.draft = prop(ns.area_a, f"Draft Road {tag}", "ASSIGNED", None, None, submitted=False)
    ns.bare = prop(ns.area_b, f"No Eval Road {tag}", "SUBMITTED", None, 50000)

    study = CatchmentStudy(property_id=ns.p1.id, status="COMPLETED", requested_by="bd_manager:asha",
                           study_geometry=from_shape(box(80.10, 12.90, 80.12, 12.92), srid=4326),
                           completed_at=now - timedelta(days=3), data_quality_flags=[])
    db.add(study)
    db.flush()
    db.add(CatchmentInsight(catchment_study_id=study.id, version=1, coverage_percentage=92.0,
                            key_findings=["Two schools within the catchment", "Heavy evening footfall"],
                            risks=["Competing supermarket 300 m away"], overall_ground_fit_score=81.5,
                            generated_by="survey_manager:meena"))
    db.commit()
    ns.db, ns.study = db, study
    yield ns
    db.rollback()
    pids = [ns.p1.id, ns.p2.id, ns.draft.id, ns.bare.id]
    db.execute(text("delete from catchment_studies where property_id = any(:p)"), {"p": pids})
    db.execute(text("delete from property_evaluations where property_id = any(:p)"), {"p": pids})
    db.execute(text("delete from property_status_history where property_id = any(:p)"), {"p": pids})
    db.execute(text("delete from properties where id = any(:p)"), {"p": pids})
    db.execute(text("delete from area_reports where area_id = any(:a)"), {"a": [ns.area_a.id, ns.area_b.id]})
    db.execute(text("delete from areas where id = any(:a)"), {"a": [ns.area_a.id, ns.area_b.id]})
    db.commit()
    db.close()


class FakeLLM:
    """Scripted model: the first call is the router, later calls are explanations."""

    def __init__(self, monkeypatch, router, explains=()):
        self.router, self.explains, self.calls = router, list(explains), []
        monkeypatch.setattr(llm, "is_configured", lambda: True)
        monkeypatch.setattr(llm, "complete", self)

    def __call__(self, system, user, max_tokens=1400):
        self.calls.append((system, user))
        if system.startswith("You route"):
            if isinstance(self.router, Exception):
                raise self.router
            return json.dumps(self.router) if not isinstance(self.router, str) else self.router
        return self.explains.pop(0) if self.explains else "Insufficient data."


# ------------------------------------------------------------------ argument validation (no database)
def test_validate_args_rejects_bad_calls():
    with pytest.raises(ValueError):
        T.validate_args("drop_tables", {})
    with pytest.raises(ValueError):
        T.validate_args("compare_areas", {"areas": ["only one"]})
    with pytest.raises(ValueError):
        T.validate_args("compare_properties", {"property_ids": [1, 2, 3, 4, 5]})
    with pytest.raises(ValueError):
        T.validate_args("search_properties", {"stage": "NOT_A_STAGE"})
    with pytest.raises(ValueError):
        T.validate_args("get_property", {})
    with pytest.raises(ValueError):
        T.validate_args("get_catchment", {})
    assert T.validate_args("search_properties", {"stage": "SUBMITTED", "limit": "3", "junk": 1}) == {
        "stage": "SUBMITTED", "limit": 3}


# ------------------------------------------------------------------ the five scenarios from the brief
@needs_db
def test_compare_two_areas(data, monkeypatch):
    a, b = data.area_a.resolved_name, data.area_b.resolved_name
    fake = FakeLLM(monkeypatch, {"calls": [{"tool": "compare_areas", "args": {"areas": [a, b]}}]},
                   [f"{a} scores 72.5 and {b} scores 64, so {a} leads by 8.5 points."])
    out = analyst.answer(data.db, PERSONA, f"Compare {a} and {b}")
    assert out["mode"] == "llm" and "8.5" in out["answer"]
    res = out["results"][0]["result"]
    assert res["best_area"] == a and res["score_gap"] == 8.5
    assert {f["key"]: f.get("leader") for f in res["factors"]} == {"population_density": a, "competition": b}
    assert [s["type"] for s in out["sources"]] == ["area_report", "area_report"]
    assert all(s["href"].startswith("#/report/") for s in out["sources"])
    assert "score_gap" in fake.calls[-1][1]  # the explanation prompt carried the verified results


@needs_db
def test_search_properties_by_area_hides_drafts_and_sorts(data, monkeypatch):
    FakeLLM(monkeypatch, {"calls": [{"tool": "search_properties",
                                     "args": {"area": data.area_a.resolved_name, "sort_by": "score"}}]},
            ["Property listing."])
    out = analyst.answer(data.db, PERSONA, "Show properties in Alpha")
    res = out["results"][0]["result"]
    assert res["total_matching"] == 2  # the unsubmitted draft is not visible to the manager
    assert [p["property_id"] for p in res["properties"]] == [data.p1.id, data.p2.id]  # best score first
    assert data.draft.id not in [p["property_id"] for p in res["properties"]]
    # "in <place>" also matches a property's locality, not only the analysed area's name
    by_locality = T.search_properties(data.db, PERSONA, area="testville")
    assert {p["property_id"] for p in by_locality["properties"]} >= {data.p1.id, data.p2.id, data.bare.id}
    assert out["sources"][0]["href"] == f"#/property/{data.p1.id}"


@needs_db
def test_compare_properties_backend_ranking(data, monkeypatch):
    ids = [data.p2.id, data.p1.id]
    r = T.compare_properties(data.db, PERSONA, ids)
    assert r["best_property_id"] == data.p1.id and r["score_gap"] == 17.2
    assert [x["property_id"] for x in r["ranking"]] == [data.p1.id, data.p2.id]
    assert r["cheapest_rent_per_sqft_property_id"] == data.p1.id
    rent = next(f for f in r["factors"] if f["key"] == "rent_affordability")
    assert rent["leader_property_id"] == data.p1.id and "no_leader" not in rent
    access = next(f for f in r["factors"] if f["key"] == "access")
    assert "leader_property_id" not in access and access["no_leader"] is True  # a tie has no leader
    # one id without an evaluation cannot be compared: reported, not invented
    r = T.compare_properties(data.db, PERSONA, [data.p1.id, data.bare.id])
    assert r["found"] is False and r["missing"] == [data.bare.id]
    FakeLLM(monkeypatch, {"calls": [{"tool": "compare_properties", "args": {"property_ids": ids}}]},
            [f"Property {data.p1.id} scores 78.4 against 61.2, a gap of 17.2 points."])
    out = analyst.answer(data.db, PERSONA, "Compare these two properties")
    assert out["mode"] == "llm" and len(out["sources"]) == 2


@needs_db
def test_property_catchment_question(data, monkeypatch):
    r = T.get_catchment(data.db, PERSONA, property_id=data.p1.id)
    assert r["found"] and r["status"] == "COMPLETED"
    assert r["insights"]["ground_fit_score"] == 81.5 and "Two schools within the catchment" in r["insights"]["key_findings"]
    assert r["survey_age_days"] == 3
    blob = json.dumps(r).lower()
    assert "work_unit" not in blob and "unit_code" not in blob and "assigned_to" not in blob  # manager view, no units
    assert T.get_catchment(data.db, PERSONA, property_id=data.p2.id)["found"] is False  # never requested
    FakeLLM(monkeypatch, {"calls": [{"tool": "get_catchment", "args": {"property_id": data.p1.id}}]},
            ["The survey found two schools and heavy evening footfall; ground-fit score 81.5."])
    out = analyst.answer(data.db, PERSONA, f"What did the ground survey find around property {data.p1.id}?")
    assert out["mode"] == "llm" and out["sources"][0]["type"] == "catchment"


@needs_db
def test_no_data_says_insufficient_without_asking_the_model(data, monkeypatch):
    fake = FakeLLM(monkeypatch, {"calls": [{"tool": "get_property_evaluation", "args": {"property_id": 987654321}},
                                            {"tool": "get_area_report", "args": {"area": "Atlantis Zzz"}}]})
    out = analyst.answer(data.db, PERSONA, "How is property 987654321 doing?")
    assert out["mode"] == "template" and out["answer"].count("Insufficient data.") == 2
    assert len(fake.calls) == 1  # only the router ran; nothing was left for the model to invent
    assert out["sources"] == []
    # a property that exists but has no evaluation, and a draft the manager must not see
    assert T.get_property_evaluation(data.db, PERSONA, data.bare.id)["found"] is False
    assert T.get_property(data.db, PERSONA, data.draft.id)["found"] is False


# ------------------------------------------------------------------ safety behaviour
@needs_db
def test_fabricated_number_is_rejected_then_template_is_used(data, monkeypatch):
    fake = FakeLLM(monkeypatch, {"calls": [{"tool": "get_property_evaluation", "args": {"property_id": data.p1.id}}]},
                   ["It scores 99.9 out of 100.", "Really, 99.9."])
    out = analyst.answer(data.db, PERSONA, "Score of property?")
    assert out["mode"] == "template" and "78.4" in out["answer"] and "99.9" not in out["answer"]
    assert len(fake.calls) == 3 and "99.9" in fake.calls[2][1]  # the retry told the model which number was rejected


@needs_db
def test_router_output_is_validated(data, monkeypatch):
    many = [{"tool": "search_areas", "args": {}}] * 5
    FakeLLM(monkeypatch, {"calls": many}, ["ok"])
    assert len(analyst.answer(data.db, PERSONA, "x")["tools_used"]) == analyst.MAX_CALLS
    FakeLLM(monkeypatch, {"calls": [{"tool": "delete_property", "args": {"property_id": 1}},
                                    {"tool": "compare_areas", "args": {"areas": ["just one"]}}]})
    out = analyst.answer(data.db, PERSONA, "x")
    assert out["mode"] == "template" and all(not x["result"]["found"] for x in out["results"])
    FakeLLM(monkeypatch, {"calls": []})
    assert analyst.answer(data.db, PERSONA, "tell me a joke")["mode"] == "help"


@needs_db
def test_llm_unavailable_and_bad_router_reply(data, monkeypatch):
    FakeLLM(monkeypatch, llm.LLMUnavailable("down"))
    assert analyst.answer(data.db, PERSONA, "x")["mode"] == "unavailable"
    FakeLLM(monkeypatch, llm.LLMBusy("rate limited"))
    assert analyst.answer(data.db, PERSONA, "x")["mode"] == "busy"
    FakeLLM(monkeypatch, "not json at all")
    assert analyst.answer(data.db, PERSONA, "x")["mode"] == "unavailable"
    monkeypatch.setattr(llm, "is_configured", lambda: False)
    assert analyst.answer(data.db, PERSONA, "x")["mode"] == "unavailable"


# ------------------------------------------------------------------ HTTP endpoint
@needs_db
def test_endpoint_permissions_limits_and_rate_limit(monkeypatch):
    from app.api.routes import analyst as route
    from app.main import app

    c = TestClient(app)
    body = {"question": "Compare A and B"}
    for persona in ("bd_executive:ravi", "survey_manager:meena", "survey_executive:karthik"):
        assert c.post("/api/analyst/chat", json=body, headers={"X-Persona": persona}).status_code == 403
    monkeypatch.setattr(llm, "is_configured", lambda: False)
    r = c.post("/api/analyst/chat", json=body, headers=MGR)
    assert r.status_code == 200 and r.json()["mode"] == "unavailable" and set(r.json()) == {
        "answer", "sources", "tools_used", "mode"}
    assert c.post("/api/analyst/chat", json={"question": "x" * 501}, headers=MGR).status_code == 422
    assert c.post("/api/analyst/chat", json={"question": "  "}, headers=MGR).status_code == 422
    monkeypatch.setattr(route, "RATE_LIMIT", 2)
    route._calls.clear()
    codes = [c.post("/api/analyst/chat", json=body, headers=MGR).status_code for _ in range(3)]
    route._calls.clear()
    assert codes == [200, 200, 429]
