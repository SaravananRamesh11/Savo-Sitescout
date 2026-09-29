import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response
from geoalchemy2.shape import from_shape, to_shape
from pydantic import BaseModel
from shapely.geometry import Point
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core import config
from app.core import property_scoring_constants as K
from app.core.db import get_db
from app.core.personas import BY_ID, current_persona, require_roles
from app.models.db_models import Area
from app.models.property_models import (Property, PropertyEvaluation, PropertyFieldCompetitor, PropertyPhoto,
                                        PropertyStatusHistory, ScoutingAssignment)
from app.services import duplicates, pipeline, property_eval, property_validation as V, storage

router = APIRouter(tags=["properties"])
PHOTO_TYPES = ["front_view", "road_view", "side_view", "parking_view", "interior_view", "building_condition"]
EDITABLE = ["address", "locality", "pincode", "total_area_sqft", "ground_floor_area_sqft", "sales_area_sqft",
            "storage_area_sqft", "frontage_ft", "road_width_ft", "floor", "number_of_floors", "property_type",
            "monthly_rent", "security_deposit", "lease_duration_months", "rent_negotiable",
            "expected_monthly_revenue", "revenue_source", "is_corner_property", "is_main_road_frontage",
            "traffic_signal_nearby", "signal_distance_m", "entry_access", "exit_access", "visibility_score",
            "two_wheeler_parking", "four_wheeler_parking", "parking_capacity", "parking_type"]
MANAGER_ONLY_FIELDS = {"expected_monthly_revenue", "revenue_source"}


class PropertyIn(BaseModel):
    lat: float
    lon: float
    location_accuracy_m: float | None = None
    location_source: str = "gps"
    area_id: int | None = None
    assignment_id: int | None = None
    address: str | None = None
    locality: str | None = None
    pincode: str | None = None
    total_area_sqft: float | None = None
    ground_floor_area_sqft: float | None = None
    sales_area_sqft: float | None = None
    storage_area_sqft: float | None = None
    frontage_ft: float | None = None
    road_width_ft: float | None = None
    floor: int | None = None
    number_of_floors: int | None = None
    property_type: str | None = None
    monthly_rent: float | None = None
    security_deposit: float | None = None
    lease_duration_months: int | None = None
    rent_negotiable: bool | None = None
    expected_monthly_revenue: float | None = None
    revenue_source: str | None = None
    is_corner_property: bool | None = None
    is_main_road_frontage: bool | None = None
    traffic_signal_nearby: bool | None = None
    signal_distance_m: float | None = None
    entry_access: str | None = None
    exit_access: str | None = None
    visibility_score: int | None = None
    two_wheeler_parking: bool | None = None
    four_wheeler_parking: bool | None = None
    parking_capacity: int | None = None
    parking_type: str | None = None
    acknowledge_duplicates: bool = False


class PropertyPatch(PropertyIn):
    lat: float | None = None  # type: ignore[assignment]
    lon: float | None = None  # type: ignore[assignment]


class TransitionIn(BaseModel):
    to_stage: str
    reason: str | None = None
    notes: str | None = None


class SubmitIn(BaseModel):
    acknowledge_duplicates: bool = False
    acknowledge_warnings: bool = False


class CompetitorIn(BaseModel):
    name: str
    kind: str = "other"
    approx_distance_m: float | None = None
    notes: str | None = None


class DupCheckIn(BaseModel):
    lat: float
    lon: float
    address: str | None = None
    pincode: str | None = None
    exclude_property_id: int | None = None


# ------------------------------------------------------------------ helpers
def _val_dict(p: Property) -> dict:
    pt = to_shape(p.location)
    d = {f: getattr(p, f) for f in EDITABLE}
    d.update(lat=pt.y, lon=pt.x, location_accuracy_m=p.location_accuracy_m)
    return d


def _can_view(p: Property, persona: dict) -> bool:
    return persona["role"] == "bd_manager" or (persona["role"] == "bd_executive" and p.created_by == persona["id"])


def _get(db: Session, pid: int, persona: dict) -> Property:
    p = db.get(Property, pid)
    if not p:
        raise HTTPException(404, "Property not found")
    if not _can_view(p, persona):
        raise HTTPException(403, "You can only view your own properties.")
    return p


def _ensure_editable(p: Property, persona: dict) -> None:
    if persona["role"] == "bd_manager":
        return
    if persona["role"] != "bd_executive" or p.created_by != persona["id"]:
        raise HTTPException(403, "You can only edit your own properties.")
    if p.pipeline_stage != "ASSIGNED":
        raise HTTPException(409, "This property has been submitted and can no longer be edited by the executive.")


def _find_area(db: Session, lat: float, lon: float, area_id: int | None, assignment: ScoutingAssignment | None) -> int:
    if assignment is not None:
        return assignment.area_id
    if area_id is not None:
        if db.get(Area, area_id) is None:
            raise HTTPException(404, "Area not found")
        return area_id
    row = db.execute(text("SELECT id FROM areas WHERE ST_Contains(geometry, ST_SetSRID(ST_MakePoint(:lon,:lat),4326)) "
                          "ORDER BY area_km2 ASC LIMIT 1"), {"lon": lon, "lat": lat}).first()
    if row is None:
        raise HTTPException(422, {"errors": [{"field": "location", "message":
                                              "This location is not inside any analysed area. Ask your manager for an "
                                              "assignment, or analyse this area first."}]})
    return row.id


def _dup_message(dups):
    return dups[0]["message"] if dups else None


def _photo_json(ph: PropertyPhoto) -> dict:
    return {"photo_id": ph.id, "photo_type": ph.photo_type, "url": storage.url_for(ph.storage_key),
            "size_bytes": ph.size_bytes, "created_at": ph.created_at.isoformat()}


def _eval_json(ev: PropertyEvaluation | None, brief: bool = False) -> dict | None:
    if ev is None:
        return None
    base = {"evaluation_id": ev.id, "version": ev.evaluation_version, "status": ev.status, "error": ev.error,
            "overall_score": ev.overall_score, "confidence": ev.confidence, "recommendation": ev.recommendation,
            "trigger": ev.trigger, "created_at": ev.created_at.isoformat(), "created_by": ev.created_by,
            "explanation_source": ev.explanation_source}
    if brief:
        return base
    return {**base, "score_breakdown": ev.score_breakdown, "insights": ev.insights, "risks": ev.risks,
            "explanation": ev.explanation, "data_sources": ev.data_sources,
            "data_quality_flags": ev.data_quality_flags, "m1_context": ev.m1_context, "metrics": ev.metrics}


def _latest_evals(db: Session, pid: int):
    rows = db.scalars(select(PropertyEvaluation).where(PropertyEvaluation.property_id == pid)
                      .order_by(PropertyEvaluation.evaluation_version.desc())).all()
    latest = rows[0] if rows else None
    done = [r for r in rows if r.status == "completed"]
    return latest, (done[0] if done else None), (done[1] if len(done) > 1 else None), rows


def summary_json(db: Session, p: Property) -> dict:
    pt = to_shape(p.location)
    latest, done, _prev, _ = _latest_evals(db, p.id)
    front = next((ph for ph in p.photos if ph.photo_type == "front_view"), p.photos[0] if p.photos else None)
    return {"property_id": p.id, "area_id": p.area_id, "area_name": p.area.resolved_name, "address": p.address,
            "locality": p.locality, "lat": pt.y, "lon": pt.x, "pipeline_stage": p.pipeline_stage,
            "monthly_rent": float(p.monthly_rent) if p.monthly_rent is not None else None,
            "total_area_sqft": p.total_area_sqft,
            "rent_per_sqft": round(float(p.monthly_rent) / p.total_area_sqft, 1) if p.monthly_rent and p.total_area_sqft else None,
            "created_by": p.created_by, "created_by_name": BY_ID.get(p.created_by, {}).get("name", p.created_by),
            "created_at": p.created_at.isoformat(), "submitted_at": p.submitted_at.isoformat() if p.submitted_at else None,
            "photo_url": storage.url_for(front.storage_key) if front else None, "photo_count": len(p.photos),
            "possible_duplicate": bool(p.duplicate_flags), "assignment_id": p.assignment_id,
            "evaluation": _eval_json(done, brief=True), "evaluation_status": latest.status if latest else None}


PRE_CATCHMENT_TRIGGERS = ("submitted", "property_updated", "manual_reevaluate")


def _final_review_view(rows: list[PropertyEvaluation], hist: list[PropertyStatusHistory]) -> dict:
    """Original M2 evaluation vs the evaluation refreshed for final review, plus the recorded final decision."""
    done = [r for r in rows if r.status == "completed"]  # newest first
    cc = [r for r in done if r.trigger == "catchment_completed"]  # newest first
    first_cc = cc[-1] if cc else None
    # "updated" = the newest finished evaluation once a final-review evaluation exists (a manual re-evaluate during
    # final review supersedes it); "original" = the last M2 evaluation made before the catchment study finished
    updated = done[0] if first_cc else None
    before = [r for r in done if r.trigger in PRE_CATCHMENT_TRIGGERS and (first_cc is None or r.created_at < first_cc.created_at)]
    original = before[0] if before else None
    by_id = {r.id: r for r in rows}
    decision = next((h for h in reversed(hist) if h.from_stage == "FINAL_REVIEW" and h.to_stage in ("APPROVED", "REJECTED")), None)
    based_on = by_id.get(decision.evaluation_id) if decision and decision.evaluation_id else None
    delta = (round(updated.overall_score - original.overall_score, 1)
             if updated and original and updated.overall_score is not None and original.overall_score is not None else None)
    return {
        "original_evaluation": _eval_json(original, brief=True), "updated_evaluation": _eval_json(updated, brief=True),
        "score_change": delta,
        "final_decision": None if decision is None else {
            "decision": decision.to_stage, "decided_by": decision.changed_by,
            "decided_by_name": BY_ID.get(decision.changed_by, {}).get("name", decision.changed_by),
            "decided_at": decision.changed_at.isoformat(), "reason": decision.reason,
            "based_on_evaluation": _eval_json(based_on, brief=True)},
    }


def detail_json(db: Session, p: Property, persona: dict) -> dict:
    pt = to_shape(p.location)
    latest, done, prev, rows = _latest_evals(db, p.id)
    hist = db.scalars(select(PropertyStatusHistory).where(PropertyStatusHistory.property_id == p.id)
                      .order_by(PropertyStatusHistory.changed_at.asc(), PropertyStatusHistory.id.asc())).all()
    out = summary_json(db, p)
    out.update({f: (float(getattr(p, f)) if f in ("monthly_rent", "security_deposit", "expected_monthly_revenue")
                    and getattr(p, f) is not None else getattr(p, f)) for f in EDITABLE})
    out.update({
        "location_accuracy_m": p.location_accuracy_m, "location_source": p.location_source,
        "duplicate_flags": p.duplicate_flags or [],
        "photos": [_photo_json(ph) for ph in p.photos],
        "field_competitors": [{"id": c.id, "name": c.name, "kind": c.kind, "approx_distance_m": c.approx_distance_m,
                               "notes": c.notes} for c in p.competitors],
        "evaluation": _eval_json(done), "previous_evaluation": _eval_json(prev, brief=True),
        "evaluation_versions": [_eval_json(r, brief=True) for r in rows],
        "latest_evaluation_status": _eval_json(latest, brief=True) if latest else None,
        "history": [{"from_stage": h.from_stage, "to_stage": h.to_stage, "changed_by": h.changed_by,
                     "changed_by_name": BY_ID.get(h.changed_by, {}).get("name", h.changed_by),
                     "changed_at": h.changed_at.isoformat(), "reason": h.reason, "notes": h.notes,
                     "evaluation_id": h.evaluation_id,
                     "evaluation_version": next((r.evaluation_version for r in rows if r.id == h.evaluation_id), None)}
                    for h in hist],
        "allowed_next_stages": pipeline.allowed_next(p.pipeline_stage, persona["role"]),
        **_final_review_view(rows, hist),
        # M3 fills this with the ground-survey insights; None means no catchment study data exists yet
        "catchment": None,
        "required_photo_types": K.REQUIRED_PHOTO_TYPES, "photo_types": PHOTO_TYPES,
        "can_edit": (persona["role"] == "bd_manager") or (p.created_by == persona["id"] and p.pipeline_stage == "ASSIGNED"),
        "storage_backend": storage.backend_name(),
    })
    return out


def _apply_fields(p: Property, data: dict, persona: dict) -> None:
    for f in EDITABLE:
        if f in data:
            if f in MANAGER_ONLY_FIELDS and persona["role"] != "bd_manager":
                raise HTTPException(403, "Only a BD Manager can set expected revenue.")
            v = data[f]
            if isinstance(v, str):
                v = v.strip() or None
            setattr(p, f, v)
    p.normalized_address = V.normalize_address(p.address)


def _plaus_or_422(d: dict) -> list[dict]:
    errors, warnings = V.plausibility(d)
    if errors:
        raise HTTPException(422, {"errors": errors, "warnings": warnings})
    return warnings


# ------------------------------------------------------------------ CRUD
@router.post("/properties", status_code=201)
def create(body: PropertyIn, db: Session = Depends(get_db), persona: dict = Depends(current_persona)):
    require_roles(persona, "bd_executive", "bd_manager")
    data = body.model_dump(exclude_unset=True)
    if body.location_source not in ("gps", "manual_pin"):
        raise HTTPException(422, "location_source must be gps or manual_pin")
    assignment = None
    if body.assignment_id is not None:
        assignment = db.get(ScoutingAssignment, body.assignment_id)
        if not assignment:
            raise HTTPException(404, "Assignment not found")
        if persona["role"] == "bd_executive" and assignment.executive_id != persona["id"]:
            raise HTTPException(403, "This assignment belongs to another executive.")
    warnings = _plaus_or_422({**data, "lat": body.lat, "lon": body.lon})
    area_id = _find_area(db, body.lat, body.lon, body.area_id, assignment)
    p = Property(area_id=area_id, assignment_id=body.assignment_id, created_by=persona["id"],
                 location=from_shape(Point(body.lon, body.lat), srid=4326),
                 location_accuracy_m=body.location_accuracy_m, location_source=body.location_source,
                 pipeline_stage="ASSIGNED", duplicate_flags=[])
    _apply_fields(p, data, persona)
    dups = duplicates.find_duplicates(db, body.lat, body.lon, normalized_address=p.normalized_address,
                                      pincode=p.pincode)
    if dups:
        p.duplicate_flags = [{"property_id": d["property_id"], "distance_m": d["distance_m"], "reason": d["reason"],
                              "acknowledged_by": persona["id"] if body.acknowledge_duplicates else None} for d in dups]
    db.add(p)
    db.flush()
    db.add(PropertyStatusHistory(property_id=p.id, from_stage=None, to_stage="ASSIGNED", changed_by=persona["id"],
                                 reason="Property captured", notes=None))
    db.commit()
    return {**detail_json(db, p, persona), "duplicates": dups, "warnings": warnings}


@router.patch("/properties/{pid}")
def patch(pid: int, body: PropertyPatch, db: Session = Depends(get_db), persona: dict = Depends(current_persona)):
    p = _get(db, pid, persona)
    _ensure_editable(p, persona)
    data = body.model_dump(exclude_unset=True)
    merged = {**_val_dict(p), **data}
    warnings = _plaus_or_422(merged)
    if body.lat is not None and body.lon is not None:
        p.location = from_shape(Point(body.lon, body.lat), srid=4326)
        if body.location_source in ("gps", "manual_pin"):
            p.location_source = body.location_source
        p.location_accuracy_m = body.location_accuracy_m
    _apply_fields(p, data, persona)
    dups = []
    if any(k in data for k in ("lat", "lon", "address", "pincode")):
        pt = to_shape(p.location)
        dups = duplicates.find_duplicates(db, pt.y, pt.x, normalized_address=p.normalized_address, pincode=p.pincode,
                                          exclude_id=p.id)
        acked = {f["property_id"] for f in (p.duplicate_flags or []) if f.get("acknowledged_by")}
        p.duplicate_flags = [{"property_id": d["property_id"], "distance_m": d["distance_m"], "reason": d["reason"],
                              "acknowledged_by": persona["id"] if (body.acknowledge_duplicates or
                                                                   d["property_id"] in acked) else None}
                             for d in dups]
    p.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {**detail_json(db, p, persona), "duplicates": dups, "warnings": warnings}


@router.get("/properties")
def list_properties(stage: str | None = None, area_id: int | None = None, mine: bool = False,
                    db: Session = Depends(get_db), persona: dict = Depends(current_persona)):
    q = select(Property).order_by(Property.updated_at.desc())
    if persona["role"] == "bd_executive" or mine:
        q = q.where(Property.created_by == persona["id"])
    elif persona["role"] != "bd_manager":
        raise HTTPException(403, "This role has no property list yet.")
    else:
        # The manager does not see drafts still being captured in the field; a property sent back for changes
        # keeps submitted_at, so it stays visible to the manager.
        q = q.where((Property.pipeline_stage != "ASSIGNED") | (Property.submitted_at.is_not(None)))
    if stage:
        q = q.where(Property.pipeline_stage == stage)
    if area_id:
        q = q.where(Property.area_id == area_id)
    return [summary_json(db, p) for p in db.scalars(q.limit(200))]


@router.get("/properties/{pid}")
def get_property(pid: int, db: Session = Depends(get_db), persona: dict = Depends(current_persona)):
    return detail_json(db, _get(db, pid, persona), persona)


@router.post("/properties/duplicate-check")
def duplicate_check(body: DupCheckIn, db: Session = Depends(get_db), persona: dict = Depends(current_persona)):
    require_roles(persona, "bd_executive", "bd_manager")
    return {"duplicates": duplicates.find_duplicates(
        db, body.lat, body.lon, normalized_address=V.normalize_address(body.address), pincode=body.pincode,
        exclude_id=body.exclude_property_id)}


# ------------------------------------------------------------------ photos and competitors
@router.post("/properties/{pid}/photos", status_code=201)
async def upload_photo(pid: int, photo_type: str = Form(...), file: UploadFile = File(...),
                       db: Session = Depends(get_db), persona: dict = Depends(current_persona)):
    p = _get(db, pid, persona)
    _ensure_editable(p, persona)
    if photo_type not in PHOTO_TYPES:
        raise HTTPException(422, f"photo_type must be one of {', '.join(PHOTO_TYPES)}")
    data = await file.read(config.MAX_PHOTO_BYTES + 1)
    if len(data) > config.MAX_PHOTO_BYTES:
        raise HTTPException(413, f"Photo is larger than {config.MAX_PHOTO_BYTES // (1024 * 1024)} MB.")
    kind = storage.sniff_image(data)
    if kind is None:
        raise HTTPException(415, "Only JPEG, PNG or WebP photos are accepted.")
    ctype, ext = kind
    key = f"properties/{p.id}/{photo_type}-{uuid.uuid4().hex}.{ext}"
    try:
        storage.put(key, data, ctype)
    except storage.StorageError as exc:
        raise HTTPException(502, str(exc))
    ph = PropertyPhoto(property_id=p.id, photo_type=photo_type, storage_key=key, content_type=ctype,
                       size_bytes=len(data), uploaded_by=persona["id"])
    db.add(ph)
    p.updated_at = datetime.now(timezone.utc)
    db.commit()
    return _photo_json(ph)


@router.delete("/properties/{pid}/photos/{photo_id}")
def delete_photo(pid: int, photo_id: int, db: Session = Depends(get_db), persona: dict = Depends(current_persona)):
    p = _get(db, pid, persona)
    _ensure_editable(p, persona)
    ph = db.get(PropertyPhoto, photo_id)
    if not ph or ph.property_id != p.id:
        raise HTTPException(404, "Photo not found")
    key = ph.storage_key
    db.delete(ph)
    db.commit()
    storage.delete(key)
    return {"deleted": photo_id}


@router.get("/photos/local/{key:path}")
def local_photo(key: str):
    """Serves the local-disk fallback only. With R2 configured, photos are served via presigned URLs instead."""
    if storage.r2_configured():
        raise HTTPException(404, "Not found")
    try:
        data, ctype = storage.read_local(key)
    except storage.StorageError:
        raise HTTPException(404, "Not found")
    return Response(content=data, media_type=ctype, headers={"Cache-Control": "private, max-age=300"})


@router.post("/properties/{pid}/competitors", status_code=201)
def add_competitor(pid: int, body: CompetitorIn, db: Session = Depends(get_db),
                   persona: dict = Depends(current_persona)):
    p = _get(db, pid, persona)
    _ensure_editable(p, persona)
    if body.kind not in ("supermarket", "organised_grocery", "convenience", "kirana", "other"):
        raise HTTPException(422, "Invalid competitor kind")
    if not body.name.strip():
        raise HTTPException(422, "Competitor name is required")
    c = PropertyFieldCompetitor(property_id=p.id, name=body.name.strip(), kind=body.kind,
                                approx_distance_m=body.approx_distance_m, notes=body.notes)
    db.add(c)
    db.commit()
    return {"id": c.id, "name": c.name, "kind": c.kind, "approx_distance_m": c.approx_distance_m, "notes": c.notes}


@router.delete("/properties/{pid}/competitors/{cid}")
def delete_competitor(pid: int, cid: int, db: Session = Depends(get_db), persona: dict = Depends(current_persona)):
    p = _get(db, pid, persona)
    _ensure_editable(p, persona)
    c = db.get(PropertyFieldCompetitor, cid)
    if not c or c.property_id != p.id:
        raise HTTPException(404, "Competitor not found")
    db.delete(c)
    db.commit()
    return {"deleted": cid}


# ------------------------------------------------------------------ submit / pipeline / evaluation
@router.post("/properties/{pid}/submit")
def submit(pid: int, body: SubmitIn, bg: BackgroundTasks, db: Session = Depends(get_db),
           persona: dict = Depends(current_persona)):
    p = _get(db, pid, persona)
    require_roles(persona, "bd_executive", "bd_manager")
    if p.pipeline_stage != "ASSIGNED":
        raise HTTPException(409, f"This property is already {p.pipeline_stage} and cannot be submitted again.")
    errors, warnings = V.validate_for_submission(_val_dict(p), {ph.photo_type for ph in p.photos})
    if errors:
        return JSONResponse(status_code=422, content={"detail": {"errors": errors, "warnings": warnings}})
    pt = to_shape(p.location)
    dups = duplicates.find_duplicates(db, pt.y, pt.x, normalized_address=p.normalized_address, pincode=p.pincode,
                                      exclude_id=p.id)
    unacked = [d for d in dups if not any(f.get("property_id") == d["property_id"] and f.get("acknowledged_by")
                                          for f in (p.duplicate_flags or []))]
    if unacked and not body.acknowledge_duplicates:
        return JSONResponse(status_code=409, content={"detail": {
            "needs": "duplicate_acknowledgement", "duplicates": dups, "warnings": warnings,
            "message": _dup_message(dups)}})
    if warnings and not body.acknowledge_warnings:
        return JSONResponse(status_code=409, content={"detail": {
            "needs": "warning_acknowledgement", "warnings": warnings, "duplicates": dups,
            "message": "Please check the warnings, then submit again to confirm."}})
    p.duplicate_flags = [{"property_id": d["property_id"], "distance_m": d["distance_m"], "reason": d["reason"],
                          "acknowledged_by": persona["id"]} for d in dups]
    p.submitted_by, p.submitted_at = persona["id"], datetime.now(timezone.utc)
    pipeline.apply_transition(db, p, "SUBMITTED", persona["id"], reason="Submitted for manager review")
    ev = property_eval.new_evaluation_row(db, p.id, "submitted", persona["id"])
    bg.add_task(property_eval.run_evaluation, ev.id)
    db.commit()
    return {**detail_json(db, p, persona), "warnings": warnings, "evaluation_id": ev.id}


@router.get("/properties/{pid}/evaluation-status")
def evaluation_status(pid: int, db: Session = Depends(get_db), persona: dict = Depends(current_persona)):
    _get(db, pid, persona)
    latest, done, _p, _r = _latest_evals(db, pid)
    return {"latest": _eval_json(latest, brief=True), "has_completed": done is not None}


def start_final_review_evaluation(db: Session, p: Property, persona_id: str, bg: BackgroundTasks) -> int:
    """Entering FINAL_REVIEW preserves an UPDATED evaluation as a new version (trigger catchment_completed).
    Earlier versions are never touched, so the manager can compare the original M2 result with the refreshed one."""
    ev = property_eval.new_evaluation_row(db, p.id, "catchment_completed", persona_id)
    bg.add_task(property_eval.run_evaluation, ev.id)
    return ev.id


@router.post("/properties/{pid}/transition")
def transition(pid: int, body: TransitionIn, bg: BackgroundTasks, db: Session = Depends(get_db),
               persona: dict = Depends(current_persona)):
    p = _get(db, pid, persona)
    evaluation_id = None
    reason = body.reason
    if not (reason or "").strip() and body.to_stage in pipeline.DEFAULT_REASON:
        reason = pipeline.DEFAULT_REASON[body.to_stage]
    if p.pipeline_stage == "FINAL_REVIEW" and body.to_stage in ("APPROVED", "REJECTED"):
        # the decision must be made on a finished evaluation, and we record which one the manager was looking at
        latest, done, _prev, _rows = _latest_evals(db, p.id)
        if latest is not None and latest.status == "running":
            raise HTTPException(409, "The updated evaluation is still running. Wait a moment, then decide.")
        if done is None:
            raise HTTPException(409, "There is no completed evaluation to base a final decision on. Re-evaluate first.")
        evaluation_id = done.id
    try:
        pipeline.apply_transition(db, p, body.to_stage, persona["id"], reason, body.notes,
                                  evaluation_id=evaluation_id)
    except pipeline.TransitionError as exc:
        raise HTTPException(exc.status, str(exc))
    if body.to_stage == "FINAL_REVIEW":
        start_final_review_evaluation(db, p, persona["id"], bg)
    p.updated_at = datetime.now(timezone.utc)
    db.commit()
    return detail_json(db, p, persona)


@router.post("/properties/{pid}/re-evaluate", status_code=202)
def re_evaluate(pid: int, bg: BackgroundTasks, db: Session = Depends(get_db), persona: dict = Depends(current_persona)):
    p = _get(db, pid, persona)
    require_roles(persona, "bd_manager")
    if p.pipeline_stage == "ASSIGNED":
        raise HTTPException(409, "Submit the property before evaluating it.")
    running = db.scalar(select(func.count()).select_from(PropertyEvaluation).where(
        PropertyEvaluation.property_id == pid, PropertyEvaluation.status == "running"))
    if running:
        raise HTTPException(409, "An evaluation is already running for this property.")
    ev = property_eval.new_evaluation_row(db, pid, "manual_reevaluate", persona["id"])
    bg.add_task(property_eval.run_evaluation, ev.id)
    return {"evaluation_id": ev.id, "version": ev.evaluation_version, "status": "running"}
