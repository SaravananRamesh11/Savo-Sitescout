"""Survey work: the Survey Executive's own units and ground captures (plus read access for the Survey Manager).

An executive sees and changes only their own units. Locations must fall inside the unit (with a small GPS
tolerance). There is no offline mode: captures are saved when submitted.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from geoalchemy2.shape import from_shape, to_shape
from pydantic import BaseModel
from shapely.geometry import Point, mapping
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import config
from app.core import survey_constants as K
from app.core.db import get_db
from app.core.personas import BY_ID, current_persona, require_roles
from app.models.db_models import Area
from app.models.property_models import Property
from app.models.survey_models import (CAPTURE_PHOTO_TYPES, CAPTURE_TYPES, CatchmentStudy, SurveyCapture,
                                      SurveyCapturePhoto, SurveyWorkUnit)
from app.services import catchment_service as svc, grid, storage, survey_schema

router = APIRouter(tags=["survey"])


class CaptureIn(BaseModel):
    capture_type: str
    data: dict
    lat: float
    lon: float
    accuracy: float | None = None


class CapturePatch(BaseModel):
    data: dict | None = None
    lat: float | None = None
    lon: float | None = None
    accuracy: float | None = None


def _unit_or_403(db: Session, uid: int, persona: dict, *, write: bool) -> SurveyWorkUnit:
    u = db.get(SurveyWorkUnit, uid)
    if u is None:
        raise HTTPException(404, "Work unit not found")
    if persona["role"] == "survey_executive":
        if u.assigned_to != persona["id"]:
            raise HTTPException(403, "This work unit is assigned to another executive.")
    elif persona["role"] == "survey_manager":
        if write:
            raise HTTPException(403, "Only the assigned Survey Executive records observations.")
    else:
        raise HTTPException(403, "This role has no survey work.")
    return u


def _inside_unit(unit: SurveyWorkUnit, lat: float, lon: float) -> bool:
    x, y = grid.lonlat_to_utm(lon, lat)
    return grid.to_utm(to_shape(unit.geometry)).buffer(K.UNIT_TOLERANCE_M).contains(Point(x, y))


def _photo_json(ph: SurveyCapturePhoto) -> dict:
    pt = to_shape(ph.location) if ph.location is not None else None
    return {"photo_id": ph.id, "photo_type": ph.photo_type, "url": storage.url_for(ph.storage_key),
            "lat": pt.y if pt else None, "lon": pt.x if pt else None, "captured_at": ph.captured_at.isoformat()}


def _capture_json(c: SurveyCapture) -> dict:
    pt = to_shape(c.location)
    return {"capture_id": c.id, "capture_type": c.capture_type, "data": c.data, "lat": pt.y, "lon": pt.x,
            "accuracy_m": c.location_accuracy_m, "captured_at": c.captured_at.isoformat(),
            "captured_by": c.captured_by, "photos": [_photo_json(p) for p in c.photos]}


def _study_label(db: Session, s: CatchmentStudy) -> dict:
    if s.property_id:
        p = db.get(Property, s.property_id)
        pt = to_shape(p.location) if p else None
        return {"label": (p.address if p and p.address else f"Property #{s.property_id}"), "locality": p.locality if p else None,
                "property_lat": pt.y if pt else None, "property_lon": pt.x if pt else None, "kind": "property"}
    a = db.get(Area, s.area_id)
    return {"label": a.resolved_name if a else f"Area #{s.area_id}", "locality": None, "kind": "area"}


def _unit_row(db: Session, u: SurveyWorkUnit, detail: bool = False) -> dict:
    out = {"unit_id": u.id, "unit_code": u.unit_code, "status": u.status, "priority": u.priority,
           "study_id": u.catchment_study_id, **_study_label(db, u.study),
           "assigned_to": u.assigned_to, "assigned_to_name": BY_ID.get(u.assigned_to, {}).get("name", u.assigned_to),
           "estimated_distance_m": u.estimated_distance_m, "target_capture_count": u.target_capture_count,
           "completed_capture_count": u.completed_capture_count,
           "workload": u.workload, "started_at": u.started_at.isoformat() if u.started_at else None,
           "completed_at": u.completed_at.isoformat() if u.completed_at else None}
    if detail:
        out["geometry"] = mapping(to_shape(u.geometry))
        out["captures"] = [_capture_json(c) for c in sorted(u.captures, key=lambda c: c.captured_at)]
        out["study_status"] = u.study.status
    return out


@router.get("/survey/form-spec")
def form_spec(persona: dict = Depends(current_persona)):
    require_roles(persona, "survey_executive", "survey_manager")
    return {"types": list(CAPTURE_TYPES), "labels": survey_schema.LABELS, "schemas": survey_schema.form_spec(),
            "photo_types": list(CAPTURE_PHOTO_TYPES), "unit_tolerance_m": K.UNIT_TOLERANCE_M}


@router.get("/survey/units")
def my_units(study_id: int | None = None, db: Session = Depends(get_db), persona: dict = Depends(current_persona)):
    require_roles(persona, "survey_executive", "survey_manager")
    q = select(SurveyWorkUnit).order_by(SurveyWorkUnit.priority, SurveyWorkUnit.catchment_study_id, SurveyWorkUnit.unit_code)
    if persona["role"] == "survey_executive":
        q = q.where(SurveyWorkUnit.assigned_to == persona["id"])
    if study_id:
        q = q.where(SurveyWorkUnit.catchment_study_id == study_id)
    return [_unit_row(db, u) for u in db.scalars(q.limit(300))]


@router.get("/survey/units/{uid}")
def unit_detail(uid: int, db: Session = Depends(get_db), persona: dict = Depends(current_persona)):
    return _unit_row(db, _unit_or_403(db, uid, persona, write=False), detail=True)


@router.post("/survey/units/{uid}/start")
def start_unit(uid: int, db: Session = Depends(get_db), persona: dict = Depends(current_persona)):
    u = _unit_or_403(db, uid, persona, write=True)
    if u.status == "COMPLETED":
        raise HTTPException(409, "This work unit is already completed.")
    svc.start_unit(db, u)
    db.commit()
    return _unit_row(db, u, detail=True)


@router.post("/survey/units/{uid}/complete")
def complete_unit(uid: int, db: Session = Depends(get_db), persona: dict = Depends(current_persona)):
    u = _unit_or_403(db, uid, persona, write=True)
    if u.status == "COMPLETED":
        raise HTTPException(409, "This work unit is already completed.")
    if u.completed_capture_count < 1:
        raise HTTPException(422, "Record at least one observation before completing this work unit.")
    svc.start_unit(db, u)  # completing implies it was started
    u.status, u.completed_at = "COMPLETED", datetime.now(timezone.utc)
    db.commit()
    note = None
    if u.target_capture_count and u.completed_capture_count < u.target_capture_count:
        note = (f"Completed with {u.completed_capture_count} of {u.target_capture_count} planned observations. "
                "Coverage will be lower and the survey manager will see it.")
    return {**_unit_row(db, u, detail=True), "note": note}


@router.post("/survey/units/{uid}/captures", status_code=201)
def add_capture(uid: int, body: CaptureIn, db: Session = Depends(get_db), persona: dict = Depends(current_persona)):
    u = _unit_or_403(db, uid, persona, write=True)
    if u.status == "COMPLETED":
        raise HTTPException(409, "This work unit is completed; observations can no longer be added.")
    if body.capture_type not in CAPTURE_TYPES:
        raise HTTPException(422, f"capture_type must be one of {', '.join(CAPTURE_TYPES)}")
    try:
        data = survey_schema.validate_capture(body.capture_type, body.data)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    if not (-90 <= body.lat <= 90 and -180 <= body.lon <= 180):
        raise HTTPException(422, "Invalid location.")
    if not _inside_unit(u, body.lat, body.lon):
        raise HTTPException(422, f"This location is outside your work unit {u.unit_code}. Move the pin inside the "
                                 "highlighted area, or ask the survey manager if the boundary looks wrong.")
    svc.start_unit(db, u)
    c = SurveyCapture(work_unit_id=u.id, captured_by=persona["id"], capture_type=body.capture_type, data=data,
                      location=from_shape(Point(body.lon, body.lat), srid=4326), location_accuracy_m=body.accuracy)
    db.add(c)
    u.completed_capture_count += 1
    db.commit()
    warn = None
    if body.accuracy and body.accuracy > K.POOR_GPS_ACCURACY_M:
        warn = f"GPS accuracy is only about {body.accuracy:.0f} m; check the pin is where you observed this."
    return {**_capture_json(c), "warning": warn, "unit": _unit_row(db, u)}


def _capture_or_403(db: Session, cid: int, persona: dict) -> SurveyCapture:
    c = db.get(SurveyCapture, cid)
    if c is None:
        raise HTTPException(404, "Observation not found")
    if persona["role"] != "survey_executive" or c.unit.assigned_to != persona["id"]:
        raise HTTPException(403, "You can only change your own observations.")
    if c.unit.status == "COMPLETED":
        raise HTTPException(409, "This work unit is completed; its observations can no longer be changed.")
    return c


@router.patch("/survey/captures/{cid}")
def edit_capture(cid: int, body: CapturePatch, db: Session = Depends(get_db), persona: dict = Depends(current_persona)):
    c = _capture_or_403(db, cid, persona)
    if body.data is not None:
        try:
            c.data = survey_schema.validate_capture(c.capture_type, body.data)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
    if body.lat is not None and body.lon is not None:
        if not _inside_unit(c.unit, body.lat, body.lon):
            raise HTTPException(422, f"This location is outside your work unit {c.unit.unit_code}.")
        c.location = from_shape(Point(body.lon, body.lat), srid=4326)
        c.location_accuracy_m = body.accuracy
    c.updated_at = datetime.now(timezone.utc)
    db.commit()
    return _capture_json(c)


@router.delete("/survey/captures/{cid}")
def delete_capture(cid: int, db: Session = Depends(get_db), persona: dict = Depends(current_persona)):
    c = _capture_or_403(db, cid, persona)
    keys = [p.storage_key for p in c.photos]
    c.unit.completed_capture_count = max(0, c.unit.completed_capture_count - 1)
    db.delete(c)
    db.commit()
    for k in keys:
        storage.delete(k)
    return {"deleted": cid}


@router.post("/survey/captures/{cid}/photos", status_code=201)
async def add_photo(cid: int, photo_type: str = Form(...), file: UploadFile = File(...), lat: float | None = Form(None),
                    lon: float | None = Form(None), accuracy: float | None = Form(None),
                    db: Session = Depends(get_db), persona: dict = Depends(current_persona)):
    c = _capture_or_403(db, cid, persona)
    if photo_type not in CAPTURE_PHOTO_TYPES:
        raise HTTPException(422, f"photo_type must be one of {', '.join(CAPTURE_PHOTO_TYPES)}")
    if len(c.photos) >= K.MAX_PHOTOS_PER_CAPTURE:
        raise HTTPException(422, f"At most {K.MAX_PHOTOS_PER_CAPTURE} photos per observation.")
    data = await file.read(config.MAX_PHOTO_BYTES + 1)
    if len(data) > config.MAX_PHOTO_BYTES:
        raise HTTPException(413, f"Photo is larger than {config.MAX_PHOTO_BYTES // (1024 * 1024)} MB.")
    kind = storage.sniff_image(data)
    if kind is None:
        raise HTTPException(415, "Only JPEG, PNG or WebP photos are accepted.")
    ctype, ext = kind
    key = f"captures/{c.id}/{photo_type}-{uuid.uuid4().hex}.{ext}"
    try:
        storage.put(key, data, ctype)
    except storage.StorageError as exc:
        raise HTTPException(502, str(exc))
    ph = SurveyCapturePhoto(capture_id=c.id, storage_key=key, photo_type=photo_type, location_accuracy_m=accuracy,
                            location=from_shape(Point(lon, lat), srid=4326) if lat is not None and lon is not None else None)
    db.add(ph)
    db.commit()
    return _photo_json(ph)


@router.delete("/survey/captures/{cid}/photos/{pid}")
def delete_photo(cid: int, pid: int, db: Session = Depends(get_db), persona: dict = Depends(current_persona)):
    c = _capture_or_403(db, cid, persona)
    ph = db.get(SurveyCapturePhoto, pid)
    if ph is None or ph.capture_id != c.id:
        raise HTTPException(404, "Photo not found")
    key = ph.storage_key
    db.delete(ph)
    db.commit()
    storage.delete(key)
    return {"deleted": pid}
