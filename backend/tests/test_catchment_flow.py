"""M3 integration tests (real database). Overpass is stubbed with a synthetic payload; photos use the local fallback."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import Point, box
from sqlalchemy import text

from app.core import config

pytestmark = pytest.mark.skipif(not config.DB_URL, reason="database url not configured")

from tests.test_catchment_split import CENTER, payload  # noqa: E402
from tests.test_property_integration import EXEC, FULL, JPEG, MGR  # noqa: E402

BDM = MGR
SM = {"X-Persona": "survey_manager:meena"}
K1 = {"X-Persona": "survey_executive:karthik"}
L1 = {"X-Persona": "survey_executive:lakshmi"}


@pytest.fixture()
def env(monkeypatch):
    from app.core.db import get_session
    from app.main import app
    from app.models.db_models import Area
    from app.services import catchment_split, overpass_client, storage

    monkeypatch.setattr(storage, "r2_configured", lambda: False)
    fake = payload()
    monkeypatch.setattr(overpass_client, "fetch_bbox", lambda *a: dict(fake, fetched_at=datetime.now(timezone.utc).isoformat()))
    catchment_split._OSM_MEMO.clear()
    db = get_session()
    tag = uuid.uuid4().hex[:8]
    x, y = CENTER
    area = Area(input_type="grid_cells", raw_input=f"m3test_{tag}", resolved_name=f"M3 test {tag}", area_km2=2.0,
                geometry=from_shape(box(x - 0.01, y - 0.01, x + 0.01, y + 0.01), srid=4326), boundary_quality="exact")
    db.add(area)
    db.commit()
    yield TestClient(app), db, area
    db.rollback()
    ids = [r[0] for r in db.execute(text("select id from properties where area_id=:a"), {"a": area.id})]
    if ids:
        db.execute(text("delete from catchment_studies where reused_from_study_id is not null and "
                        "(property_id = any(:ids) or area_id=:a)"), {"ids": ids, "a": area.id})
        db.execute(text("delete from catchment_studies where property_id = any(:ids) or area_id=:a"), {"ids": ids, "a": area.id})
        for t in ("property_evaluations", "property_status_history", "property_photos", "property_field_competitors"):
            db.execute(text(f"delete from {t} where property_id = any(:ids)"), {"ids": ids})
    else:
        db.execute(text("delete from catchment_studies where area_id=:a"), {"a": area.id})
    db.execute(text("delete from properties where area_id=:a"), {"a": area.id})
    db.execute(text("delete from scouting_assignments where area_id=:a"), {"a": area.id})
    db.execute(text("delete from areas where id=:a"), {"a": area.id})
    db.commit()
    db.close()


def _submitted_property(c, area, address, dx=0.0, dy=0.0):
    x, y = CENTER
    r = c.post("/api/properties", json={"lat": y + dy, "lon": x + dx, "area_id": area.id, "address": address,
                                        "locality": "Velachery"}, headers=EXEC)
    assert r.status_code == 201, r.text
    pid = r.json()["property_id"]
    assert c.patch(f"/api/properties/{pid}", json=FULL, headers=EXEC).status_code == 200
    for t in ("front_view", "road_view", "interior_view"):
        assert c.post(f"/api/properties/{pid}/photos", data={"photo_type": t},
                      files={"file": ("a.jpg", JPEG, "image/jpeg")}, headers=EXEC).status_code == 201
    r = c.post(f"/api/properties/{pid}/submit", json={"acknowledge_warnings": True, "acknowledge_duplicates": True}, headers=EXEC)
    assert r.status_code == 200, r.text
    return pid


def _request_catchment(c, pid, **extra):
    assert c.post(f"/api/properties/{pid}/transition", json={"to_stage": "UNDER_REVIEW"}, headers=BDM).status_code == 200
    r = c.post(f"/api/properties/{pid}/transition", json={"to_stage": "CATCHMENT_REQUESTED", **extra}, headers=BDM)
    assert r.status_code == 200, r.text
    return r.json()


def _insert_captures(db, unit_id, n, who):
    """Bulk-create valid captures directly (fast) so the study reaches the coverage needed for insights."""
    from app.models.survey_models import SurveyCapture, SurveyWorkUnit

    u = db.get(SurveyWorkUnit, unit_id)
    poly = to_shape(u.geometry)
    pt = poly.representative_point()
    kinds = [("residential", {"independent_houses": 5, "activity_level": "medium"}),
             ("commercial", {"business_kind": "grocery", "activity_level": "high"}),
             ("traffic", {"pedestrian": "high", "vehicle": "medium", "observation_period": "evening"}),
             ("accessibility", {"road_condition": "good", "entry_exit": "easy", "parking": "moderate"}),
             ("competition", {"name": f"Store {unit_id}", "competitor_type": "supermarket", "size": "medium", "customer_activity": "medium"}),
             ("demand_generator", {"kind": "school", "name": "School"})]
    for i in range(n):
        t, d = kinds[i % len(kinds)]
        db.add(SurveyCapture(work_unit_id=u.id, captured_by=who, capture_type=t, data=d,
                             location=from_shape(Point(pt.x + 0.00002 * i, pt.y), srid=4326)))
    u.completed_capture_count += n
    db.commit()


def test_full_flow_request_split_capture_complete_final_review(env):
    c, db, area = env
    pid = _submitted_property(c, area, "1 Ground Survey Road")
    prop = _request_catchment(c, pid)  # no reason typed: still fine
    assert prop["pipeline_stage"] == "CATCHMENT_REQUESTED"
    assert prop["catchment"]["status"] == "REQUESTED" and "insights" not in prop["catchment"]
    sid = prop["catchment"]["study_id"]

    # BD manager sees catchment-level status only, never units; executives cannot list studies
    bd = c.get(f"/api/catchments/{sid}", headers=BDM).json()
    assert bd["status"] == "REQUESTED" and "units" not in bd and "progress" not in bd
    assert c.get("/api/catchments", headers=K1).status_code == 403

    # survey manager splits and assigns
    prev = c.post(f"/api/catchments/{sid}/split-preview", json={"units": 3}, headers=SM)
    assert prev.status_code == 200, prev.text
    pv = prev.json()
    assert len(pv["units"]) == 3 and pv["meta"]["balance_ratio"] < 3 and pv["units"][0]["workload"]["lanes"]
    assert c.post(f"/api/catchments/{sid}/work-units", json={"units": 3, "assignments": ["survey_executive:karthik"]}, headers=SM).status_code == 422
    r = c.post(f"/api/catchments/{sid}/work-units", json={"units": 3, "assignments": [
        "survey_executive:karthik", "survey_executive:lakshmi", "survey_executive:karthik"]}, headers=SM)
    assert r.status_code == 201, r.text
    assert c.post(f"/api/catchments/{sid}/work-units", json={"units": 3, "assignments": ["survey_executive:karthik"] * 3}, headers=SM).status_code == 409

    # each executive sees only their own units
    k_units = c.get("/api/survey/units", headers=K1).json()
    l_units = c.get("/api/survey/units", headers=L1).json()
    assert len(k_units) == 2 and len(l_units) == 1 and all(u["assigned_to"] == "survey_executive:karthik" for u in k_units)
    mine = k_units[0]["unit_id"]
    assert c.get(f"/api/survey/units/{l_units[0]['unit_id']}", headers=K1).status_code == 403
    assert c.post(f"/api/survey/units/{mine}/start", headers=L1).status_code == 403

    # captures: outside the unit is refused, inside works, bad data is refused
    detail = c.get(f"/api/survey/units/{mine}", headers=K1).json()
    from shapely.geometry import shape
    inside = shape(detail["geometry"]).representative_point()
    body = {"capture_type": "traffic", "data": {"pedestrian": "high", "vehicle": "low", "observation_period": "morning"},
            "lat": inside.y, "lon": inside.x, "accuracy": 12}
    far = c.post(f"/api/survey/units/{mine}/captures", json={**body, "lat": inside.y + 0.05}, headers=K1)
    assert far.status_code == 422 and "outside your work unit" in far.json()["detail"]
    assert c.post(f"/api/survey/units/{mine}/captures", json={**body, "data": {"pedestrian": "busy"}}, headers=K1).status_code == 422
    ok = c.post(f"/api/survey/units/{mine}/captures", json=body, headers=K1)
    assert ok.status_code == 201, ok.text
    cap = ok.json()
    assert cap["unit"]["status"] == "IN_PROGRESS" and cap["unit"]["completed_capture_count"] == 1
    # optional photo evidence on a capture
    ph = c.post(f"/api/survey/captures/{cap['capture_id']}/photos", data={"photo_type": "road_condition"},
                files={"file": ("a.jpg", JPEG, "image/jpeg")}, headers=K1)
    assert ph.status_code == 201 and "url" in ph.json()
    # the first started unit moved the study and the property
    assert c.get(f"/api/catchments/{sid}", headers=SM).json()["status"] == "IN_PROGRESS"
    assert c.get(f"/api/properties/{pid}", headers=BDM).json()["pipeline_stage"] == "CATCHMENT_IN_PROGRESS"
    # cannot complete the study while units are open, and cannot complete a unit with no captures
    assert c.post(f"/api/catchments/{sid}/complete", headers=SM).status_code == 409
    empty_unit = k_units[1]["unit_id"] if k_units[1]["unit_id"] != mine else k_units[0]["unit_id"]
    assert c.post(f"/api/survey/units/{empty_unit}/complete", headers=K1).status_code == 422

    # fill every unit with real observations (bulk insert for speed), complete the units
    for u in k_units + l_units:
        need = max(u["target_capture_count"] - (1 if u["unit_id"] == mine else 0), 6)
        _insert_captures(db, u["unit_id"], need, u["assigned_to"])
    for hdr, units in ((K1, k_units), (L1, l_units)):
        for u in units:
            assert c.post(f"/api/survey/units/{u['unit_id']}/complete", headers=hdr).status_code == 200
    # completed units cannot be edited any more
    assert c.post(f"/api/survey/units/{mine}/captures", json=body, headers=K1).status_code == 409

    # survey manager reviews (preview is not stored) and completes: property goes to CATCHMENT_COMPLETED
    pre = c.get(f"/api/catchments/{sid}/insights-preview", headers=SM).json()
    assert pre["preview"] is True and pre["coverage_percentage"] >= 70
    assert c.get(f"/api/catchments/{sid}/insights", headers=SM).json() == []  # nothing stored yet
    done = c.post(f"/api/catchments/{sid}/complete", headers=SM)
    assert done.status_code == 200, done.text
    v1 = done.json()["insights"]
    assert v1["version"] == 1 and v1["coverage_percentage"] >= 70
    p = c.get(f"/api/properties/{pid}", headers=BDM).json()
    assert p["pipeline_stage"] == "CATCHMENT_COMPLETED"
    assert p["catchment"]["status"] == "COMPLETED" and p["catchment"]["insights"]["version"] == 1
    assert p["catchment"]["evidence_photos"], "the evidence photo is visible to the BD manager"
    assert "units" not in p["catchment"]

    # insights are versioned: regenerate adds v2 and leaves v1 untouched
    v2 = c.post(f"/api/catchments/{sid}/insights", headers=SM)
    assert v2.status_code == 201 and v2.json()["version"] == 2
    versions = c.get(f"/api/catchments/{sid}/insights", headers=SM).json()
    assert [v["version"] for v in versions] == [2, 1]
    assert versions[1]["key_findings"] == v1["key_findings"]

    # final review: the M2 score is unchanged; the survey findings travel with the new evaluation version
    before = c.get(f"/api/properties/{pid}", headers=BDM).json()["evaluation"]
    fr = c.post(f"/api/properties/{pid}/transition", json={"to_stage": "FINAL_REVIEW"}, headers=BDM)
    assert fr.status_code == 200
    d = c.get(f"/api/properties/{pid}", headers=BDM).json()
    assert d["evaluation"]["trigger"] == "catchment_completed"
    assert d["evaluation"]["overall_score"] == before["overall_score"]  # M2 scoring untouched by M3
    assert d["evaluation"]["metrics"]["catchment"]["coverage_percentage"] >= 70
    assert any(r["factor"] == "ground_survey" for r in d["evaluation"]["explanation"]["reasons"])
    assert d["catchment"]["insights"]["ground_fit_score"] is not None
    ap = c.post(f"/api/properties/{pid}/transition", json={"to_stage": "APPROVED", "reason": "Ground survey confirms footfall"}, headers=BDM)
    assert ap.status_code == 200 and ap.json()["final_decision"]["decision"] == "APPROVED"

    # reuse: a second property inside the surveyed catchment reuses it (new study row, no copied captures)
    pid2 = _submitted_property(c, area, "2 Nearby Reuse Road", dx=0.0006, dy=0.0004)
    n_caps = db.execute(text("select count(*) from survey_captures")).scalar()
    p2 = _request_catchment(c, pid2)
    assert p2["pipeline_stage"] == "CATCHMENT_COMPLETED", p2["pipeline_stage"]
    assert p2["catchment"]["reused"] is True and p2["catchment"]["reused_from_study_id"] == sid
    assert "Reused catchment study" in p2["catchment"]["reuse_reason"]
    assert p2["catchment"]["insights"]["version"] == 2  # latest version of the study that holds the data
    assert db.execute(text("select count(*) from survey_captures")).scalar() == n_caps  # nothing duplicated
    assert db.execute(text("select count(*) from survey_work_units where catchment_study_id=:s"), {"s": p2["catchment"]["study_id"]}).scalar() == 0
    hist = [(h["to_stage"], h["changed_by"]) for h in p2["history"]][-3:]
    assert hist[0][0] == "CATCHMENT_REQUESTED" and hist[1][0] == "CATCHMENT_IN_PROGRESS" and hist[2][0] == "CATCHMENT_COMPLETED"


def test_reuse_rule_and_force_new(env):
    from app.models.survey_models import CatchmentInsight, CatchmentStudy
    from app.services import catchment_service as svc, grid

    c, db, area = env
    x, y = CENTER
    x0, y0 = grid.lonlat_to_utm(x, y)
    geom = grid.to_ll(Point(x0, y0).buffer(500, 32))

    def study(completed_days_ago=10, cov=90.0, flags=None, geometry=None):
        s = CatchmentStudy(area_id=area.id, study_geometry=from_shape(geometry or geom, srid=4326), status="COMPLETED",
                           requested_by="bd_manager:asha", started_at=datetime.now(timezone.utc),
                           completed_at=datetime.now(timezone.utc) - timedelta(days=completed_days_ago), data_quality_flags=[])
        db.add(s)
        db.flush()
        db.add(CatchmentInsight(catchment_study_id=s.id, version=1, coverage_percentage=cov, generated_by="survey_manager:meena",
                                data_quality_flags=flags or []))
        db.commit()
        return s

    assert svc.find_reusable(db, geom) is None
    old = study(completed_days_ago=200)  # too old
    assert svc.find_reusable(db, geom) is None
    db.delete(old); db.commit()
    poor = study(cov=40.0)  # poor data coverage
    assert svc.find_reusable(db, geom) is None
    bad = study(cov=95.0, flags=["insufficient_data"])
    assert svc.find_reusable(db, geom) is None
    db.delete(poor); db.delete(bad); db.commit()
    far = study(geometry=grid.to_ll(Point(x0 + 5000, y0).buffer(500, 32)))  # somewhere else
    assert svc.find_reusable(db, geom) is None
    db.delete(far); db.commit()
    good = study()
    hit = svc.find_reusable(db, geom)
    assert hit is not None and hit[0].id == good.id and hit[1] > 0.99 and hit[2] == 10
    # a study covering only a part of the new catchment is not good enough
    half = grid.to_ll(Point(x0 + 800, y0).buffer(500, 32))
    assert svc.find_reusable(db, half) is None


def test_area_study_rules(env):
    c, db, area = env
    # the M1 area is small enough; asking twice returns the same open study, and only a BD manager may ask
    assert c.post("/api/catchments", json={"area_id": area.id}, headers=SM).status_code == 403
    r = c.post("/api/catchments", json={"area_id": area.id}, headers=BDM)
    assert r.status_code == 201 and r.json()["outcome"] == "created" and r.json()["target"] == "area"
    again = c.post("/api/catchments", json={"area_id": area.id}, headers=BDM)
    assert again.json()["outcome"] == "existing" and again.json()["study_id"] == r.json()["study_id"]
    assert c.post("/api/catchments", json={"area_id": area.id, "property_id": 1}, headers=BDM).status_code == 422
    assert c.post("/api/catchments", json={}, headers=BDM).status_code == 422
    assert c.get(f"/api/areas/{area.id}/catchment", headers=BDM).json()["study_id"] == r.json()["study_id"]
    # exactly-one-target is also enforced by the database
    with pytest.raises(Exception):
        db.execute(text("insert into catchment_studies (status, study_geometry, requested_by, data_quality_flags) values "
                        "('REQUESTED', ST_GeomFromText('POLYGON((0 0,0 1,1 1,0 0))',4326), 'x', '[]')"))
        db.commit()
    db.rollback()
