from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from geoalchemy2.shape import to_shape
from shapely.geometry import mapping
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.agent.graph import initial_steps, run_report
from app.core.db import get_db
from app.models.db_models import Area, AreaReport, GridCell
from app.models.schemas import GenerateRequest

router = APIRouter(tags=["reports"])


def summary(r: AreaReport) -> dict:
    return {"report_id": r.id, "area_id": r.area_id, "area_name": r.area.resolved_name,
            "input_type": r.area.input_type, "area_km2": round(r.area.area_km2, 2), "status": r.status,
            "created_at": r.created_at.isoformat(),
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
            "overall_score": r.overall_score, "rating": r.rating, "data_quality_flags": r.data_quality_flags or [],
            "explanation_source": r.explanation_source}


def status_json(r: AreaReport) -> dict:
    return {"report_id": r.id, "status": r.status, "steps": r.steps or [], "failed_node": r.failed_node,
            "error": r.error}


@router.post("/reports/generate", status_code=202)
def generate(req: GenerateRequest, bg: BackgroundTasks, db: Session = Depends(get_db)):
    area = db.get(Area, req.area_id)
    if not area:
        raise HTTPException(404, "Area not found; resolve it first")
    report = AreaReport(area_id=area.id, status="queued", steps=initial_steps())
    db.add(report)
    db.commit()
    bg.add_task(run_report, report.id)  # Overpass + LLM latency: never block the request
    return {"report_id": report.id, "status": "queued"}


@router.post("/reports/{report_id}/retry", status_code=202)
def retry(report_id: int, bg: BackgroundTasks, db: Session = Depends(get_db)):
    r = db.get(AreaReport, report_id)
    if not r:
        raise HTTPException(404, "Report not found")
    if r.status not in ("failed",):
        raise HTTPException(409, f"Report is {r.status}; only failed reports can be retried")
    r.status = "queued"
    r.steps = initial_steps()
    db.commit()
    bg.add_task(run_report, r.id)  # external data is served from cache, so a retry is fast
    return {"report_id": r.id, "status": "queued"}


@router.get("/reports/compare")
def compare(ids: str = Query(..., description="comma separated report ids"), db: Session = Depends(get_db)):
    try:
        rid = [int(x) for x in ids.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(422, "ids must be integers")
    if not 2 <= len(rid) <= 4:
        raise HTTPException(422, "Compare 2 to 4 reports")
    found = {r.id: r for r in db.scalars(select(AreaReport).options(joinedload(AreaReport.area))
                                           .where(AreaReport.id.in_(rid)))}
    reports = [found.get(i) for i in rid]
    if any(r is None or r.status != "completed" for r in reports):
        raise HTTPException(404, "All reports must exist and be completed")
    keys = [f["key"] for f in reports[0].score_breakdown]
    factors = []
    for k in keys:
        row = {"key": k}
        for r in reports:
            f = next(x for x in r.score_breakdown if x["key"] == k)
            row.setdefault("label", f["label"])
            row.setdefault("weight", f["weight"])
            row[str(r.id)] = {"points": f["points"], "raw": f["raw"], "unit": f["unit"]}
        factors.append(row)
    best = max(reports, key=lambda r: r.overall_score)
    return {"reports": [{**summary(r), "profile": {k: v for k, v in (r.area_profile or {}).items()
                                                  if k not in ("hotspots", "facts")},
                         "hotspots": (r.area_profile or {}).get("hotspots", [])} for r in reports],
            "factors": factors, "best_report_id": best.id}


@router.get("/reports")
def list_reports(area_id: int | None = None, limit: int = 50, db: Session = Depends(get_db)):
    q = select(AreaReport).options(joinedload(AreaReport.area)).order_by(AreaReport.created_at.desc()).limit(min(limit, 200))
    if area_id:
        q = q.where(AreaReport.area_id == area_id)
    return [summary(r) for r in db.scalars(q)]


@router.get("/reports/{report_id}/status")
def status(report_id: int, db: Session = Depends(get_db)):
    r = db.get(AreaReport, report_id)
    if not r:
        raise HTTPException(404, "Report not found")
    return status_json(r)


@router.get("/reports/{report_id}")
def detail(report_id: int, include_cells: bool = True, db: Session = Depends(get_db)):
    r = db.scalar(select(AreaReport).options(joinedload(AreaReport.area)).where(AreaReport.id == report_id))
    if not r:
        raise HTTPException(404, "Report not found")
    out = {**summary(r), **status_json(r), "score_breakdown": r.score_breakdown, "explanation": r.llm_explanation,
           "data_sources": r.data_sources, "area_geometry": mapping(to_shape(r.area.geometry))}
    prof = r.area_profile or {}
    out["profile"] = {k: v for k, v in prof.items() if k not in ("hotspots", "facts")}
    out["hotspots"] = prof.get("hotspots", [])
    if include_cells and r.status == "completed":
        cells = db.scalars(select(GridCell).where(GridCell.report_id == r.id)).all()
        out["cells"] = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "id": c.cell_id,
             "properties": {"cell_id": c.cell_id, "score": c.score, "coverage": c.coverage,
                            "is_hotspot": c.is_hotspot, "hotspot_rank": c.hotspot_rank},
             "geometry": mapping(to_shape(c.geometry))} for c in cells]}
    return out


@router.get("/reports/{report_id}/cells/{cell_id}")
def cell_detail(report_id: int, cell_id: str, db: Session = Depends(get_db)):
    c = db.scalar(select(GridCell).where(GridCell.report_id == report_id, GridCell.cell_id == cell_id))
    if not c:
        raise HTTPException(404, "Cell not found in this report")
    return {"cell_id": c.cell_id, "score": c.score, "coverage": c.coverage, "score_breakdown": c.score_breakdown,
            "features": c.features, "is_hotspot": c.is_hotspot, "hotspot_rank": c.hotspot_rank,
            "locality": c.locality_name, "nearest_named_road": c.nearest_named_road, "why": c.why}
