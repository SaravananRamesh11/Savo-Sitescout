"""Integration tests for the M2 API against the configured database (skipped when no db url is set).

Photos go to the local-disk fallback and Overpass is stubbed, so the test is offline apart from Postgres.
"""
import uuid

import pytest
from fastapi.testclient import TestClient
from geoalchemy2.shape import from_shape
from shapely.geometry import box

from app.core import config

pytestmark = pytest.mark.skipif(not config.DB_URL, reason="database url not configured")

JPEG = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffd9")
EXEC = {"X-Persona": "bd_executive:ravi"}
MGR = {"X-Persona": "bd_manager:asha"}
FULL = {"pincode": "600042", "total_area_sqft": 1800, "ground_floor_area_sqft": 1200, "sales_area_sqft": 1300,
        "storage_area_sqft": 400, "frontage_ft": 24, "road_width_ft": 40, "floor": 0, "number_of_floors": 2,
        "property_type": "shop", "monthly_rent": 145000, "rent_negotiable": True, "is_corner_property": True,
        "is_main_road_frontage": True, "traffic_signal_nearby": False, "entry_access": "easy",
        "exit_access": "moderate", "visibility_score": 4, "two_wheeler_parking": True, "four_wheeler_parking": True,
        "parking_capacity": 4, "parking_type": "on_property"}


@pytest.fixture()
def env(monkeypatch):
    from app.core.db import get_session
    from app.main import app
    from app.models.db_models import Area
    from app.services import overpass_client, storage

    monkeypatch.setattr(storage, "r2_configured", lambda: False)
    monkeypatch.setattr(overpass_client, "fetch_bbox",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("offline in tests")))
    db = get_session()
    tag = uuid.uuid4().hex[:8]
    area = Area(input_type="grid_cells", raw_input=f"m2test_{tag}", resolved_name=f"M2 test {tag}", area_km2=1.0,
                geometry=from_shape(box(80.2200, 12.9800, 80.2300, 12.9900), srid=4326), boundary_quality="exact")
    db.add(area)
    db.commit()
    client = TestClient(app)
    yield client, db, area
    db.rollback()
    from sqlalchemy import text

    ids = [r[0] for r in db.execute(text("select id from properties where area_id=:a"), {"a": area.id})]
    if ids:  # M3: catchment studies point at properties with ON DELETE RESTRICT, so they go first
        db.execute(text("delete from catchment_studies where reused_from_study_id is not null and property_id = any(:ids)"), {"ids": ids})
        db.execute(text("delete from catchment_studies where property_id = any(:ids)"), {"ids": ids})
    for t in ("property_evaluations", "property_status_history", "property_photos", "property_field_competitors"):
        if ids:
            db.execute(text(f"delete from {t} where property_id = any(:ids)"), {"ids": ids})
    db.execute(text("delete from properties where area_id=:a"), {"a": area.id})
    db.execute(text("delete from scouting_assignments where area_id=:a"), {"a": area.id})
    db.execute(text("delete from areas where id=:a"), {"a": area.id})
    db.commit()
    db.close()


def _draft(c, area, lat=12.9850, lon=80.2250, **extra):
    body = {"lat": lat, "lon": lon, "area_id": area.id, "address": "12 Test Road", "locality": "Velachery", **extra}
    r = c.post("/api/properties", json=body, headers=EXEC)
    assert r.status_code == 201, r.text
    return r.json()


def _complete(c, pid):
    assert c.patch(f"/api/properties/{pid}", json=FULL, headers=EXEC).status_code == 200
    for t in ("front_view", "road_view", "interior_view"):
        r = c.post(f"/api/properties/{pid}/photos", data={"photo_type": t}, files={"file": ("a.jpg", JPEG, "image/jpeg")},
                   headers=EXEC)
        assert r.status_code == 201, r.text


def test_draft_submit_validation_evaluation_and_versions(env):
    c, db, area = env
    p = _draft(c, area)
    pid = p["property_id"]
    assert p["pipeline_stage"] == "ASSIGNED"

    r = c.post(f"/api/properties/{pid}/submit", json={}, headers=EXEC)  # incomplete -> per-field errors
    assert r.status_code == 422
    fields = {e["field"] for e in r.json()["detail"]["errors"]}
    assert {"monthly_rent", "photo:front_view", "entry_access"} <= fields

    _complete(c, pid)
    r = c.post(f"/api/properties/{pid}/submit", json={"acknowledge_warnings": True}, headers=EXEC)
    assert r.status_code == 200, r.text
    d = c.get(f"/api/properties/{pid}", headers=MGR).json()  # background evaluation has finished by now
    assert d["pipeline_stage"] == "SUBMITTED" and d["evaluation"]["version"] == 1
    ev1 = d["evaluation"]
    assert ev1["status"] == "completed" and ev1["explanation_source"] == "template"
    keys = {f["key"]: f for f in ev1["score_breakdown"]}
    assert keys["rent_affordability"]["scored"] is False  # no revenue supplied -> Insufficient data
    assert keys["demand_generators"]["scored"] is False and "osm_unavailable" in ev1["data_quality_flags"]

    # the manager supplies expected revenue and re-evaluates -> a NEW version; version 1 is untouched
    assert c.patch(f"/api/properties/{pid}", json={"expected_monthly_revenue": 3_000_000,
                                                    "revenue_source": "manager assumption"}, headers=MGR).status_code == 200
    assert c.post(f"/api/properties/{pid}/re-evaluate", headers=MGR).status_code == 202
    d2 = c.get(f"/api/properties/{pid}", headers=MGR).json()
    assert d2["evaluation"]["version"] == 2 and d2["previous_evaluation"]["version"] == 1
    assert {v["version"] for v in d2["evaluation_versions"]} == {1, 2}
    assert d2["previous_evaluation"]["overall_score"] == ev1["overall_score"]
    rent2 = {f["key"]: f for f in d2["evaluation"]["score_breakdown"]}["rent_affordability"]
    assert rent2["scored"] is True

    # executives cannot edit after submit, cannot review, and cannot set revenue
    assert c.patch(f"/api/properties/{pid}", json={"monthly_rent": 1}, headers=EXEC).status_code == 409
    assert c.post(f"/api/properties/{pid}/transition", json={"to_stage": "UNDER_REVIEW"}, headers=EXEC).status_code == 403


def test_duplicates_are_flagged_never_merged(env):
    c, db, area = env
    a = _draft(c, area, lat=12.98500, lon=80.22500)
    b = c.post("/api/properties", json={"lat": 12.98506, "lon": 80.22506, "area_id": area.id,
                                        "address": "12 Test Rd"}, headers=EXEC).json()
    assert b["property_id"] != a["property_id"]  # both rows exist: no upsert
    assert b["duplicates"] and b["duplicates"][0]["property_id"] == a["property_id"]
    assert "Possible duplicate property found" in b["duplicates"][0]["message"]
    far = c.post("/api/properties", json={"lat": 12.98600, "lon": 80.22600, "area_id": area.id}, headers=EXEC).json()
    assert far["duplicates"] == []  # ~150 m away
    n = len(c.get("/api/properties", headers=EXEC).json())  # drafts are visible to the executive who owns them
    assert n >= 3


def test_duplicate_must_be_acknowledged_to_submit(env):
    c, db, area = env
    a = _draft(c, area, lat=12.98500, lon=80.22500)
    _complete(c, a["property_id"])
    b = c.post("/api/properties", json={"lat": 12.98505, "lon": 80.22505, "area_id": area.id,
                                        "address": "Somewhere else", "locality": "Velachery"}, headers=EXEC).json()
    _complete(c, b["property_id"])
    r = c.post(f"/api/properties/{b['property_id']}/submit", json={}, headers=EXEC)
    assert r.status_code == 409 and r.json()["detail"]["needs"] == "duplicate_acknowledgement"
    r = c.post(f"/api/properties/{b['property_id']}/submit",
               json={"acknowledge_duplicates": True, "acknowledge_warnings": True}, headers=EXEC)
    assert r.status_code == 200
    assert r.json()["duplicate_flags"][0]["acknowledged_by"] == "bd_executive:ravi"


def test_no_reject_before_catchment_and_catchment_request_needs_no_text(env):
    c, db, area = env
    ids = []
    for i in range(2):
        p = _draft(c, area, lat=12.9850 + i * 0.004, lon=80.2250, address=f"{i + 40} Distinct Street {i}")
        _complete(c, p["property_id"])
        assert c.post(f"/api/properties/{p['property_id']}/submit", json={"acknowledge_warnings": True},
                      headers=EXEC).status_code == 200
        ids.append(p["property_id"])
    a, b = ids
    for pid in ids:
        # rejecting is not possible while the property is only submitted
        r = c.post(f"/api/properties/{pid}/transition", json={"to_stage": "REJECTED", "reason": "x"}, headers=MGR)
        assert r.status_code == 409
        assert c.post(f"/api/properties/{pid}/transition", json={"to_stage": "UNDER_REVIEW"}, headers=MGR).status_code == 200
        r = c.post(f"/api/properties/{pid}/transition", json={"to_stage": "REJECTED", "reason": "Rent too high"}, headers=MGR)
        assert r.status_code == 409  # still no reject before the catchment is done
        d = c.get(f"/api/properties/{pid}", headers=MGR).json()
        assert set(d["allowed_next_stages"]) == {"ASSIGNED", "CATCHMENT_REQUESTED"}
    # the catchment request works with no typed text and records an automatic reason
    r = c.post(f"/api/properties/{b}/transition", json={"to_stage": "CATCHMENT_REQUESTED"}, headers=MGR)
    assert r.status_code == 200 and r.json()["pipeline_stage"] == "CATCHMENT_REQUESTED"
    hist = [(h["from_stage"], h["to_stage"]) for h in r.json()["history"]]
    assert hist == [(None, "ASSIGNED"), ("ASSIGNED", "SUBMITTED"), ("SUBMITTED", "UNDER_REVIEW"),
                    ("UNDER_REVIEW", "CATCHMENT_REQUESTED")]
    assert r.json()["history"][-1]["reason"] == "Catchment study requested"
    assert r.json()["allowed_next_stages"] == []  # the manager waits for the survey; no reject option
    r = c.post(f"/api/properties/{b}/transition", json={"to_stage": "REJECTED", "reason": "x"}, headers=MGR)
    assert r.status_code == 409
    # send back for changes still needs a reason, and the manager still sees the sent-back property
    assert c.post(f"/api/properties/{a}/transition", json={"to_stage": "ASSIGNED"}, headers=MGR).status_code == 422
    r = c.post(f"/api/properties/{a}/transition", json={"to_stage": "ASSIGNED", "reason": "Add a road photo"}, headers=MGR)
    assert r.status_code == 200 and r.json()["pipeline_stage"] == "ASSIGNED"
    assert a in {x["property_id"] for x in c.get("/api/properties", headers=MGR).json()}


def test_manager_list_hides_unsubmitted_drafts_but_executive_keeps_them(env):
    c, db, area = env
    draft = _draft(c, area, address="1 Draft Only Road")["property_id"]  # never submitted
    assert draft not in {x["property_id"] for x in c.get("/api/properties", headers=MGR).json()}
    assert draft in {x["property_id"] for x in c.get("/api/properties", headers=EXEC).json()}
    assert c.get("/api/properties?stage=ASSIGNED", headers=MGR).json() == [] or all(
        x["submitted_at"] for x in c.get("/api/properties?stage=ASSIGNED", headers=MGR).json())


def test_photo_rules_and_roles(env):
    c, db, area = env
    p = _draft(c, area)
    pid = p["property_id"]
    bad = c.post(f"/api/properties/{pid}/photos", data={"photo_type": "front_view"},
                 files={"file": ("a.jpg", b"definitely not an image", "image/jpeg")}, headers=EXEC)
    assert bad.status_code == 415
    ok = c.post(f"/api/properties/{pid}/photos", data={"photo_type": "front_view"},
                files={"file": ("a.jpg", JPEG, "image/jpeg")}, headers=EXEC)
    assert ok.status_code == 201 and "storage_key" not in ok.json() and ok.json()["url"]
    other = {"X-Persona": "bd_executive:divya"}  # another executive cannot see or change it
    assert c.get(f"/api/properties/{pid}", headers=other).status_code == 403
    assert c.post("/api/assignments", json={"area_id": area.id, "executive_id": "bd_executive:ravi"},
                  headers=EXEC).status_code == 403


def test_assignment_flow_and_location_outside_chennai(env):
    c, db, area = env
    r = c.post("/api/assignments", json={"area_id": area.id, "executive_id": "bd_executive:ravi",
                                         "hotspot_lat": 12.985, "hotspot_lon": 80.225, "hotspot_label": "Test hotspot"},
               headers=MGR)
    assert r.status_code == 201
    aid = r.json()["assignment_id"]
    mine = c.get("/api/assignments", headers=EXEC).json()
    assert any(a["assignment_id"] == aid for a in mine)
    assert not any(a["assignment_id"] == aid for a in c.get("/api/assignments", headers={"X-Persona": "bd_executive:divya"}).json())
    p = c.post("/api/properties", json={"lat": 12.985, "lon": 80.225, "assignment_id": aid}, headers=EXEC).json()
    assert p["area_id"] == area.id and p["assignment_id"] == aid
    r = c.post("/api/properties", json={"lat": 19.07, "lon": 72.87, "area_id": area.id}, headers=EXEC)
    assert r.status_code == 422 and r.json()["detail"]["errors"][0]["field"] == "location"


def test_final_review_preserves_updated_evaluation_and_decision(env):
    from app.models.property_models import Property
    from app.services import pipeline

    c, db, area = env
    p = _draft(c, area, address="9 Final Review Street")
    pid = p["property_id"]
    _complete(c, pid)
    assert c.post(f"/api/properties/{pid}/submit", json={"acknowledge_warnings": True}, headers=EXEC).status_code == 200
    assert c.post(f"/api/properties/{pid}/transition", json={"to_stage": "UNDER_REVIEW"}, headers=MGR).status_code == 200
    r = c.post(f"/api/properties/{pid}/transition", json={"to_stage": "CATCHMENT_REQUESTED", "reason": "Validate footfall"}, headers=MGR)
    assert r.status_code == 200
    # the manager cannot skip the survey stages (M3 / system drives them)
    assert c.post(f"/api/properties/{pid}/transition", json={"to_stage": "FINAL_REVIEW", "reason": "x"}, headers=MGR).status_code == 409

    prop = db.get(Property, pid)
    for st in ("CATCHMENT_IN_PROGRESS", "CATCHMENT_COMPLETED"):
        pipeline.apply_transition(db, prop, st, "system:m3-test", reason="test")
    db.commit()
    v_before = c.get(f"/api/properties/{pid}", headers=MGR).json()["evaluation"]["version"]

    r = c.post(f"/api/properties/{pid}/transition", json={"to_stage": "FINAL_REVIEW"}, headers=MGR)
    assert r.status_code == 200 and r.json()["pipeline_stage"] == "FINAL_REVIEW"
    d = c.get(f"/api/properties/{pid}", headers=MGR).json()  # the background evaluation has finished
    assert d["evaluation"]["trigger"] == "catchment_completed" and d["evaluation"]["version"] == v_before + 1
    assert d["original_evaluation"]["version"] == v_before  # the original M2 result is still there, untouched
    assert d["updated_evaluation"]["version"] == v_before + 1
    assert d["score_change"] is not None
    # the requested study exists but its survey never ran in this test: no findings are shown, none are invented
    assert d["catchment"]["status"] == "REQUESTED" and "insights" not in d["catchment"]
    assert set(d["allowed_next_stages"]) == {"APPROVED", "REJECTED"}

    assert c.post(f"/api/properties/{pid}/transition", json={"to_stage": "APPROVED"}, headers=MGR).status_code == 422  # reason needed
    assert c.post(f"/api/properties/{pid}/transition", json={"to_stage": "APPROVED", "reason": "ok"}, headers=EXEC).status_code == 403
    r = c.post(f"/api/properties/{pid}/transition", json={"to_stage": "APPROVED", "reason": "Footfall confirmed; rent acceptable"}, headers=MGR)
    assert r.status_code == 200 and r.json()["pipeline_stage"] == "APPROVED"
    fd = r.json()["final_decision"]
    assert fd["decision"] == "APPROVED" and fd["reason"] == "Footfall confirmed; rent acceptable"
    assert fd["based_on_evaluation"]["version"] == v_before + 1  # the decision remembers what the manager saw
    assert fd["decided_by"] == "bd_manager:asha"
    last = r.json()["history"][-1]
    assert (last["from_stage"], last["to_stage"], last["evaluation_version"]) == ("FINAL_REVIEW", "APPROVED", v_before + 1)
    assert c.post(f"/api/properties/{pid}/transition", json={"to_stage": "REJECTED", "reason": "changed my mind"}, headers=MGR).status_code == 409
