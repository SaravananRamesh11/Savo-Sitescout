"""Opportunity Finder (BD Manager only): city-wide, ranked, unscouted high-potential 500 m cells. See
services/opportunity_finder.py. Nothing here creates a property, an assignment or a catchment study."""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.personas import current_persona, require_roles
from app.models.opportunity_models import OpportunityRun
from app.services import opportunity_finder as finder

router = APIRouter(tags=["opportunities"])


def _status(run: OpportunityRun) -> dict:
    return {"run_id": run.id, "status": run.status, "progress": run.progress, "error": run.error,
            "created_at": run.created_at.isoformat(),
            "completed_at": run.completed_at.isoformat() if run.completed_at else None}


def map_payload(run: OpportunityRun) -> dict:
    """Compact city payload: one polygon (4 corners) and a score per cell, the top results in full, and provenance."""
    cells = run.cells or []
    corners = finder.cell_corners_ll([(c["col"], c["row"]) for c in cells])
    summary = {k: v for k, v in (run.summary or {}).items() if k not in ("coverage", "tiles", "city_break")}
    return {**_status(run), "config": run.config, "summary": summary, "top": run.top or [],
            "data_quality_flags": run.data_quality_flags or [], "data_sources": run.data_sources or [],
            "reasons": finder.REASONS,
            "cells": [{"id": c["id"], "p": p, "o": c["opp"], "c": c["cov"], "w": c["why"]} for c, p in zip(cells, corners)]}


def _get_run(db: Session, run_id: int) -> OpportunityRun:
    run = db.get(OpportunityRun, run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    return run


@router.post("/opportunities/runs", status_code=202)
def start(bg: BackgroundTasks, refresh: bool = False, db: Session = Depends(get_db),
          persona: dict = Depends(current_persona)):
    require_roles(persona, "bd_manager")
    active = finder.active_run(db)
    if active is not None:  # a second click joins the run that is already going
        return {"run_id": active.id, "status": active.status, "already_running": True}
    run = finder.create_run(db, persona["id"])
    bg.add_task(finder.run_finder, run.id, refresh)
    return {"run_id": run.id, "status": run.status, "already_running": False}


@router.get("/opportunities/runs/latest")
def latest(db: Session = Depends(get_db), persona: dict = Depends(current_persona)):
    require_roles(persona, "bd_manager")
    active = finder.active_run(db)
    done = db.scalar(select(OpportunityRun).where(OpportunityRun.status == "completed").order_by(OpportunityRun.id.desc()))
    return {"active": _status(active) if active else None, "run": map_payload(done) if done else None}


@router.get("/opportunities/runs/{run_id}/status")
def status(run_id: int, db: Session = Depends(get_db), persona: dict = Depends(current_persona)):
    require_roles(persona, "bd_manager")
    return _status(_get_run(db, run_id))


@router.get("/opportunities/runs/{run_id}")
def get_run(run_id: int, db: Session = Depends(get_db), persona: dict = Depends(current_persona)):
    require_roles(persona, "bd_manager")
    run = _get_run(db, run_id)
    if run.status != "completed":
        raise HTTPException(409, "This run has not finished yet.")
    return map_payload(run)


@router.get("/opportunities/runs/{run_id}/cells/{cell_id}")
def cell(run_id: int, cell_id: str, db: Session = Depends(get_db), persona: dict = Depends(current_persona)):
    require_roles(persona, "bd_manager")
    run = _get_run(db, run_id)
    if run.status != "completed":
        raise HTTPException(409, "This run has not finished yet.")
    top = next((t for t in run.top or [] if t["cell_id"] == cell_id), None)
    if top is not None:
        return {**top, "ranked": True}
    rec = next((c for c in run.cells or [] if c["id"] == cell_id), None)
    if rec is None:
        raise HTTPException(404, "Cell not found in this run")
    if not rec["ok"]:
        return {"cell_id": cell_id, "ranked": False, "reason": rec["why"], "reason_text": finder.REASONS.get(rec["why"]),
                "lat": rec["lat"], "lon": rec["lon"]}
    return {**finder.build_detail(db, run, rec), "ranked": True, "rank": None}
