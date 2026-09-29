from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from geoalchemy2.shape import to_shape
from pydantic import BaseModel
from shapely.geometry import mapping
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.personas import BY_ID, EXECUTIVES, current_persona, require_roles
from app.models.db_models import Area, AreaReport
from app.models.property_models import Property, ScoutingAssignment

router = APIRouter(tags=["assignments"])


class AssignmentIn(BaseModel):
    area_id: int
    executive_id: str
    source_report_id: int | None = None
    hotspot_cell_id: str | None = None
    hotspot_lat: float | None = None
    hotspot_lon: float | None = None
    hotspot_label: str | None = None
    notes: str | None = None
    due_date: date | None = None


class AssignmentPatch(BaseModel):
    status: str | None = None
    notes: str | None = None


def hotspot_info(db: Session, a: ScoutingAssignment, cache: dict | None = None) -> dict:
    """Locality and nearest named road of the hotspot, read from the source M1 report (a snapshot, never copied)."""
    if a.source_report_id is None or a.hotspot_cell_id is None:
        return {}
    cache = cache if cache is not None else {}
    if a.source_report_id not in cache:
        rep = db.get(AreaReport, a.source_report_id)
        cache[a.source_report_id] = (rep.area_profile or {}).get("hotspots", []) if rep else []
    for h in cache[a.source_report_id]:
        if h.get("cell_id") == a.hotspot_cell_id:
            return {"hotspot_locality": h.get("locality"), "hotspot_road": h.get("nearest_named_road")}
    return {}


def out(a: ScoutingAssignment, n_props: int = 0, info: dict | None = None) -> dict:
    return {**(info or {}), "assignment_id": a.id, "area_id": a.area_id, "area_name": a.area.resolved_name,
            "area_geometry": mapping(to_shape(a.area.geometry)), "source_report_id": a.source_report_id,
            "hotspot_cell_id": a.hotspot_cell_id, "hotspot_lat": a.hotspot_lat, "hotspot_lon": a.hotspot_lon,
            "hotspot_label": a.hotspot_label, "executive_id": a.executive_id,
            "executive_name": BY_ID.get(a.executive_id, {}).get("name", a.executive_id), "assigned_by": a.assigned_by,
            "notes": a.notes, "due_date": a.due_date.isoformat() if a.due_date else None, "status": a.status,
            "created_at": a.created_at.isoformat(), "properties_captured": n_props}


@router.get("/executives")
def executives():
    return [{"id": p["id"], "name": p["name"]} for p in EXECUTIVES]


@router.post("/assignments", status_code=201)
def create(body: AssignmentIn, db: Session = Depends(get_db), persona: dict = Depends(current_persona)):
    require_roles(persona, "bd_manager")
    if body.executive_id not in BY_ID or BY_ID[body.executive_id]["role"] != "bd_executive":
        raise HTTPException(422, "Choose a BD Executive to assign this to.")
    area = db.get(Area, body.area_id)
    if not area:
        raise HTTPException(404, "Area not found")
    if body.source_report_id is not None and db.get(AreaReport, body.source_report_id) is None:
        raise HTTPException(404, "Source report not found")
    a = ScoutingAssignment(**body.model_dump(), assigned_by=persona["id"], status="OPEN")
    db.add(a)
    db.commit()
    return out(a)


@router.get("/assignments")
def list_assignments(mine: bool = False, status: str | None = None, db: Session = Depends(get_db),
                     persona: dict = Depends(current_persona)):
    q = select(ScoutingAssignment).order_by(ScoutingAssignment.created_at.desc())
    if mine or persona["role"] == "bd_executive":
        q = q.where(ScoutingAssignment.executive_id == persona["id"])
    if status:
        q = q.where(ScoutingAssignment.status == status)
    rows = db.scalars(q).all()
    counts = dict(db.execute(select(Property.assignment_id, func.count()).where(
        Property.assignment_id.in_([r.id for r in rows] or [0])).group_by(Property.assignment_id)).all())
    cache: dict = {}
    return [out(a, counts.get(a.id, 0), hotspot_info(db, a, cache)) for a in rows]


@router.get("/assignments/{aid}")
def get_one(aid: int, db: Session = Depends(get_db), persona: dict = Depends(current_persona)):
    a = db.get(ScoutingAssignment, aid)
    if not a:
        raise HTTPException(404, "Assignment not found")
    if persona["role"] == "bd_executive" and a.executive_id != persona["id"]:
        raise HTTPException(403, "This assignment belongs to another executive.")
    n = db.scalar(select(func.count()).select_from(Property).where(Property.assignment_id == a.id)) or 0
    return out(a, n, hotspot_info(db, a))


@router.patch("/assignments/{aid}")
def patch(aid: int, body: AssignmentPatch, db: Session = Depends(get_db), persona: dict = Depends(current_persona)):
    a = db.get(ScoutingAssignment, aid)
    if not a:
        raise HTTPException(404, "Assignment not found")
    if persona["role"] == "bd_executive" and a.executive_id != persona["id"]:
        raise HTTPException(403, "This assignment belongs to another executive.")
    if body.status is not None:
        if body.status not in ("OPEN", "DONE", "CANCELLED"):
            raise HTTPException(422, "Invalid status")
        if body.status == "CANCELLED" and persona["role"] != "bd_manager":
            raise HTTPException(403, "Only a BD Manager can cancel an assignment.")
        a.status = body.status
    if body.notes is not None:
        a.notes = body.notes
    db.commit()
    return out(a)
