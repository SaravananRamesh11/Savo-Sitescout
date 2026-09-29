"""Catchment studies. Role-shaped on purpose:
BD Manager   -> catchment-level status, reuse info, and (once COMPLETED) insights + evidence. No unit information.
Survey Manager -> every study, its work units, executive progress, split preview, completion.
Survey Executive -> nothing here (their work is under /survey).
"""
from fastapi import APIRouter, Depends, HTTPException
from geoalchemy2.shape import from_shape, to_shape
from pydantic import BaseModel, Field
from shapely.geometry import mapping
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.personas import BY_ID, SURVEY_EXECUTIVES, current_persona, require_roles
from app.models.db_models import Area
from app.models.property_models import Property
from app.models.survey_models import CatchmentInsight, CatchmentStudy, SurveyWorkUnit
from app.services import catchment_insights, catchment_service as svc, catchment_split

router = APIRouter(tags=["catchments"])


class StudyIn(BaseModel):
    property_id: int | None = None
    area_id: int | None = None
    force_new: bool = False


class PreviewIn(BaseModel):
    units: int | None = Field(default=None, ge=1, le=12)


class UnitsIn(BaseModel):
    units: int | None = Field(default=None, ge=1, le=12)
    assignments: list[str]  # one survey-executive persona id per unit, in unit order
    priorities: list[int] | None = None


def _label(db: Session, s: CatchmentStudy) -> dict:
    if s.property_id:
        p = db.get(Property, s.property_id)
        return {"label": (p.address or f"Property #{p.id}") if p else f"Property #{s.property_id}",
                "sublabel": (p.locality if p else None), "kind": "property"}
    a = db.get(Area, s.area_id)
    return {"label": a.resolved_name if a else f"Area #{s.area_id}", "sublabel": "area study", "kind": "area"}


def _unit_json(u: SurveyWorkUnit, with_geometry: bool = True) -> dict:
    out = {"unit_id": u.id, "unit_code": u.unit_code, "status": u.status, "priority": u.priority,
           "assigned_to": u.assigned_to, "assigned_to_name": BY_ID.get(u.assigned_to, {}).get("name", u.assigned_to),
           "estimated_distance_m": u.estimated_distance_m, "target_capture_count": u.target_capture_count,
           "completed_capture_count": u.completed_capture_count, "workload": u.workload,
           "started_at": u.started_at.isoformat() if u.started_at else None,
           "completed_at": u.completed_at.isoformat() if u.completed_at else None}
    if with_geometry:
        out["geometry"] = mapping(to_shape(u.geometry))
    return out


def _study_for_manager(db: Session, s: CatchmentStudy, persona: dict) -> dict:
    out = {**svc.study_status_json(db, s), **_label(db, s), "study_geometry": mapping(to_shape(s.study_geometry))}
    if persona["role"] == "survey_manager":
        units = db.scalars(select(SurveyWorkUnit).where(SurveyWorkUnit.catchment_study_id == s.id)
                           .order_by(SurveyWorkUnit.unit_code)).all()
        if s.reused_from_study_id:
            units = []  # a reused study has no units of its own
        out["units"] = [_unit_json(u) for u in units]
        tgt = sum(u.target_capture_count or 0 for u in units)
        out["progress"] = {"units_total": len(units), "units_completed": sum(1 for u in units if u.status == "COMPLETED"),
                           "captures": sum(u.completed_capture_count for u in units), "target": tgt,
                           "coverage_percentage": catchment_insights.coverage(
                               [{"target": u.target_capture_count, "completed": u.completed_capture_count} for u in units])}
        out["can_split"] = s.status == "REQUESTED" and not units and not s.reused_from_study_id
        out["can_complete"] = bool(units) and s.status != "COMPLETED" and all(u.status == "COMPLETED" for u in units)
    return out


def _get_study(db: Session, sid: int) -> CatchmentStudy:
    s = db.get(CatchmentStudy, sid)
    if s is None:
        raise HTTPException(404, "Catchment study not found")
    return s


@router.post("/catchments", status_code=201)
def request_study(body: StudyIn, db: Session = Depends(get_db), persona: dict = Depends(current_persona)):
    require_roles(persona, "bd_manager")
    if (body.property_id is None) == (body.area_id is None):
        raise HTTPException(422, "Request a study for a property or for an area, not both.")
    prop = area = None
    if body.property_id is not None:
        prop = db.get(Property, body.property_id)
        if prop is None:
            raise HTTPException(404, "Property not found")
    else:
        area = db.get(Area, body.area_id)
        if area is None:
            raise HTTPException(404, "Area not found")
    try:
        study, outcome = svc.request_study(db, persona["id"], prop=prop, area=area, force_new=body.force_new)
        if outcome == "reused":
            svc.apply_reuse_to_property(db, study)
    except svc.StudyError as exc:
        raise HTTPException(exc.status, str(exc))
    db.commit()
    return {**_study_for_manager(db, study, persona), "outcome": outcome}


@router.get("/catchments")
def list_studies(status: str | None = None, db: Session = Depends(get_db), persona: dict = Depends(current_persona)):
    require_roles(persona, "bd_manager", "survey_manager")
    q = select(CatchmentStudy).order_by(CatchmentStudy.created_at.desc()).limit(200)
    if status:
        q = q.where(CatchmentStudy.status == status)
    out = []
    for s in db.scalars(q):
        row = {**svc.study_status_json(db, s), **_label(db, s)}
        if persona["role"] == "survey_manager":
            units = db.scalars(select(SurveyWorkUnit).where(SurveyWorkUnit.catchment_study_id == s.id)).all()
            row["units_total"] = len(units)
            row["units_completed"] = sum(1 for u in units if u.status == "COMPLETED")
            row["needs_split"] = s.status == "REQUESTED" and not units and not s.reused_from_study_id
        out.append(row)
    return out


@router.get("/catchments/{sid}")
def get_study(sid: int, db: Session = Depends(get_db), persona: dict = Depends(current_persona)):
    require_roles(persona, "bd_manager", "survey_manager")
    s = _get_study(db, sid)
    out = _study_for_manager(db, s, persona)
    if s.status == "COMPLETED" or persona["role"] == "survey_manager":
        ins = svc.latest_insight(db, s)
        out["insights"] = svc.insight_json(ins)
    if s.status == "COMPLETED":
        out["evidence_photos"] = svc.evidence_photos(db, s)
    return out


@router.get("/areas/{area_id}/catchment")
def area_catchment(area_id: int, db: Session = Depends(get_db), persona: dict = Depends(current_persona)):
    require_roles(persona, "bd_manager", "survey_manager")
    s = db.scalar(select(CatchmentStudy).where(CatchmentStudy.area_id == area_id)
                  .order_by(CatchmentStudy.created_at.desc()).limit(1))
    return None if s is None else {**svc.study_status_json(db, s), **_label(db, s)}


def _split(db: Session, s: CatchmentStudy, units: int | None, fresh: bool = False):
    poly = to_shape(s.study_geometry)
    area_id = None
    if s.property_id:
        area_id = db.get(Property, s.property_id).area_id
    else:
        area_id = s.area_id
    payload, flags = catchment_split.load_osm(db, s.id, poly, area_id, fresh=fresh)
    res = catchment_split.compute_split(poly, payload, units)
    res["meta"]["flags"] = sorted(set(res["meta"]["flags"] + flags))
    return res


@router.post("/catchments/{sid}/split-preview")
def split_preview(sid: int, body: PreviewIn, db: Session = Depends(get_db), persona: dict = Depends(current_persona)):
    require_roles(persona, "survey_manager")
    s = _get_study(db, sid)
    if s.reused_from_study_id:
        raise HTTPException(409, "This study reuses an existing survey, so there is nothing to split.")
    if s.status != "REQUESTED" or db.scalar(select(func.count()).select_from(SurveyWorkUnit).where(
            SurveyWorkUnit.catchment_study_id == sid)):
        raise HTTPException(409, "This study already has work units.")
    try:
        res = _split(db, s, body.units, fresh=True)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    # open workload per executive so the suggestion evens out people who are already busy
    open_load: dict[str, float] = {}
    for u in db.scalars(select(SurveyWorkUnit).where(SurveyWorkUnit.status != "COMPLETED")):
        open_load[u.assigned_to] = open_load.get(u.assigned_to, 0.0) + float((u.workload or {}).get("points", 0))
    suggested = catchment_split.suggest_assignees(res["units"], SURVEY_EXECUTIVES, open_load)
    units = []
    for u, who in zip(res["units"], suggested):
        units.append({"index": u["index"], "unit_code": u["unit_code"], "geometry": mapping(u["geometry_ll"]),
                      "estimated_distance_m": u["estimated_distance_m"], "target_capture_count": u["target_capture_count"],
                      "workload": u["workload"], "suggested_assignee": who,
                      "suggested_assignee_name": BY_ID[who]["name"]})
    return {"study_id": sid, "units": units, "meta": res["meta"], "study_geometry": mapping(to_shape(s.study_geometry)),
            "executives": [{"id": e["id"], "name": e["name"], "open_points": round(open_load.get(e["id"], 0.0), 1)}
                           for e in SURVEY_EXECUTIVES]}


@router.post("/catchments/{sid}/work-units", status_code=201)
def create_units(sid: int, body: UnitsIn, db: Session = Depends(get_db), persona: dict = Depends(current_persona)):
    require_roles(persona, "survey_manager")
    s = _get_study(db, sid)
    if s.reused_from_study_id or s.status != "REQUESTED" or db.scalar(
            select(func.count()).select_from(SurveyWorkUnit).where(SurveyWorkUnit.catchment_study_id == sid)):
        raise HTTPException(409, "Work units can only be created once, for a study that is still REQUESTED.")
    try:
        res = _split(db, s, body.units)  # deterministic: uses the OSM data the manager just previewed
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    n = len(res["units"])
    if len(body.assignments) != n:
        raise HTTPException(422, f"The split has {n} units; assign an executive to each ({len(body.assignments)} given).")
    for who in body.assignments:
        if who not in BY_ID or BY_ID[who]["role"] != "survey_executive":
            raise HTTPException(422, f"'{who}' is not a Survey Executive.")
    prios = body.priorities or [2] * n
    if len(prios) != n or any(p not in (1, 2, 3) for p in prios):
        raise HTTPException(422, "Priorities must be 1 to 3, one per unit.")
    created = []
    for u, who, pr in zip(res["units"], body.assignments, prios):
        wu = SurveyWorkUnit(catchment_study_id=sid, unit_code=u["unit_code"], geometry=from_shape(u["geometry_ll"], srid=4326),
                            unit_type="lane_zone", assigned_to=who, status="ASSIGNED", priority=pr,
                            estimated_distance_m=u["estimated_distance_m"], target_capture_count=u["target_capture_count"],
                            completed_capture_count=0, workload={**u["workload"], "meta_flags": res["meta"]["flags"]})
        db.add(wu)
        created.append(wu)
    if res["meta"]["flags"]:
        s.data_quality_flags = sorted(set((s.data_quality_flags or []) + res["meta"]["flags"]))
    db.commit()
    return {"study_id": sid, "units": [_unit_json(u, with_geometry=False) for u in created], "meta": res["meta"]}


@router.get("/catchments/{sid}/insights-preview")
def insights_preview(sid: int, db: Session = Depends(get_db), persona: dict = Depends(current_persona)):
    require_roles(persona, "survey_manager")
    s = _get_study(db, sid)
    if s.reused_from_study_id:
        raise HTTPException(409, "This study reuses an existing survey.")
    return {**catchment_insights_preview(db, s), "study_id": sid}


def catchment_insights_preview(db: Session, s: CatchmentStudy) -> dict:
    agg = svc.aggregate_for_study(db, s)
    return {"preview": True, "coverage_percentage": agg["coverage_percentage"],
            "residential": agg["residential_summary"], "commercial": agg["commercial_summary"],
            "competition": agg["competition_summary"], "traffic": agg["traffic_summary"],
            "accessibility": agg["accessibility_summary"], "demand_generators": agg["demand_generator_summary"],
            "key_findings": agg["key_findings"], "risks": agg["risks"], "ground_fit_score": agg["overall_ground_fit_score"],
            "data_quality_flags": agg["data_quality_flags"]}


@router.post("/catchments/{sid}/complete")
def complete(sid: int, db: Session = Depends(get_db), persona: dict = Depends(current_persona)):
    require_roles(persona, "survey_manager")
    s = _get_study(db, sid)
    if s.reused_from_study_id:
        raise HTTPException(409, "This study reuses an existing survey.")
    try:
        ins = svc.complete_study(db, s, persona["id"])
    except svc.StudyError as exc:
        raise HTTPException(exc.status, str(exc))
    db.commit()
    return {**_study_for_manager(db, s, persona), "insights": svc.insight_json(ins)}


@router.get("/catchments/{sid}/insights")
def insight_versions(sid: int, db: Session = Depends(get_db), persona: dict = Depends(current_persona)):
    require_roles(persona, "bd_manager", "survey_manager")
    s = _get_study(db, sid)
    if persona["role"] == "bd_manager" and s.status != "COMPLETED":
        raise HTTPException(409, "The catchment survey is not completed yet.")
    root = svc.data_study(db, s)
    rows = db.scalars(select(CatchmentInsight).where(CatchmentInsight.catchment_study_id == root.id)
                      .order_by(CatchmentInsight.version.desc())).all()
    return [svc.insight_json(r) for r in rows]


@router.post("/catchments/{sid}/insights", status_code=201)
def regenerate_insights(sid: int, db: Session = Depends(get_db), persona: dict = Depends(current_persona)):
    """New insight version for a COMPLETED study (for example after a late correction). Older versions stay."""
    require_roles(persona, "survey_manager")
    s = _get_study(db, sid)
    if s.status != "COMPLETED" or s.reused_from_study_id:
        raise HTTPException(409, "Only a completed study that holds its own survey data can be regenerated.")
    ins = svc.new_insight_version(db, s, persona["id"])
    db.commit()
    return svc.insight_json(ins)
