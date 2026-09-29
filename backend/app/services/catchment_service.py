"""Catchment study lifecycle: request (with reuse), unit start, completion, and the manager-facing summary.

Study status drives the M2 property stage through the existing state machine, as the `system` actor:
  first unit starts  -> property CATCHMENT_IN_PROGRESS
  study completed    -> property CATCHMENT_COMPLETED
Reuse never copies survey captures: a new study row points at the study that holds the data.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import Point
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import survey_constants as K
from app.models.db_models import Area
from app.models.property_models import Property
from app.models.survey_models import (CatchmentInsight, CatchmentStudy, SurveyCapture, SurveyCapturePhoto,
                                      SurveyWorkUnit)
from app.services import catchment_insights, grid, pipeline, storage

SYSTEM = "system:catchment"


class StudyError(ValueError):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


# ------------------------------------------------------------------ geometry and reuse
def study_geometry(prop: Property | None = None, area: Area | None = None):
    if prop is not None:
        p = to_shape(prop.location)
        x, y = grid.lonlat_to_utm(p.x, p.y)
        return grid.to_ll(Point(x, y).buffer(K.CATCHMENT_RADIUS_M, 32))
    poly = to_shape(area.geometry)
    if grid.area_km2(poly) > K.STUDY_MAX_KM2:
        raise StudyError(f"This area is {grid.area_km2(poly):.1f} km2, above the {K.STUDY_MAX_KM2:g} km2 limit for one "
                         "catchment study. Request the study for a property or a smaller area.", 422)
    return poly


def data_study(db: Session, study: CatchmentStudy) -> CatchmentStudy:
    """The study that actually holds the survey data (a reused study points at it; chains are collapsed)."""
    seen = set()
    while study.reused_from_study_id and study.id not in seen:
        seen.add(study.id)
        study = db.get(CatchmentStudy, study.reused_from_study_id)
    return study


def latest_insight(db: Session, study: CatchmentStudy) -> CatchmentInsight | None:
    root = data_study(db, study)
    return db.scalar(select(CatchmentInsight).where(CatchmentInsight.catchment_study_id == root.id)
                     .order_by(CatchmentInsight.version.desc()).limit(1))


def find_reusable(db: Session, geom_ll, now: datetime | None = None) -> tuple[CatchmentStudy, float, int, float] | None:
    """Best completed, fresh, well-covered, good-quality study for this geometry: (study, geometry_coverage,
    age_days, data_coverage) or None. Simple deterministic rule, thresholds in survey_constants."""
    now = now or datetime.now(timezone.utc)
    new_utm = grid.to_utm(geom_ll)
    if new_utm.area <= 0:
        return None
    cands = db.scalars(select(CatchmentStudy).where(
        CatchmentStudy.status == "COMPLETED", CatchmentStudy.reused_from_study_id.is_(None),
        CatchmentStudy.completed_at >= now - timedelta(days=K.REUSE_MAX_AGE_DAYS),
        func.ST_Intersects(CatchmentStudy.study_geometry, func.ST_GeomFromText(geom_ll.wkt, 4326)))).all()
    best = None
    for s in cands:
        ins = latest_insight(db, s)
        if ins is None or ins.coverage_percentage / 100 < K.REUSE_MIN_DATA_COVERAGE or \
                "insufficient_data" in (ins.data_quality_flags or []):
            continue
        cov = new_utm.intersection(grid.to_utm(to_shape(s.study_geometry))).area / new_utm.area
        if cov < K.REUSE_MIN_COVERAGE:
            continue
        key = (cov, s.completed_at)
        if best is None or key > best[0]:
            best = (key, s, cov, ins.coverage_percentage)
    if best is None:
        return None
    _, s, cov, dcov = best
    return s, cov, (now - s.completed_at).days, dcov


# ------------------------------------------------------------------ property sync
def sync_property(db: Session, study: CatchmentStudy, to_stage: str, reason: str) -> None:
    """Move the linked M2 property through the state machine as the system actor (skipped if not applicable)."""
    if study.property_id is None:
        return
    prop = db.get(Property, study.property_id)
    if prop is None or to_stage not in pipeline.TRANSITIONS.get(prop.pipeline_stage, {}):
        return
    pipeline.apply_transition(db, prop, to_stage, SYSTEM, reason=reason)
    prop.updated_at = datetime.now(timezone.utc)


# ------------------------------------------------------------------ request
def request_study(db: Session, requested_by: str, *, prop: Property | None = None, area: Area | None = None,
                  force_new: bool = False) -> tuple[CatchmentStudy, str]:
    """Create the study row (or reuse). Returns (study, outcome) with outcome in {created, reused, existing}."""
    assert (prop is None) != (area is None), "exactly one of property / area"
    target = (CatchmentStudy.property_id == prop.id) if prop is not None else (CatchmentStudy.area_id == area.id)
    existing = db.scalar(select(CatchmentStudy).where(target, CatchmentStudy.status.in_(["REQUESTED", "IN_PROGRESS"])))
    if existing is not None:
        return existing, "existing"
    geom = study_geometry(prop, area)
    hit = None if force_new else find_reusable(db, geom)
    study = CatchmentStudy(property_id=prop.id if prop else None, area_id=area.id if area else None,
                           study_geometry=from_shape(geom, srid=4326), requested_by=requested_by, status="REQUESTED",
                           data_quality_flags=[])
    if hit is None:
        db.add(study)
        db.flush()
        return study, "created"
    src, cov, age, dcov = hit
    now = datetime.now(timezone.utc)
    study.status, study.started_at, study.completed_at = "COMPLETED", now, now
    study.reused_from_study_id = src.id
    study.data_quality_flags = ["reused_study"]
    study.reuse_reason = (f"Reused catchment study #{src.id}: completed {age} day(s) ago (limit {K.REUSE_MAX_AGE_DAYS}), "
                          f"covers {cov * 100:.0f}% of this catchment (needs {K.REUSE_MIN_COVERAGE * 100:.0f}%), "
                          f"ground data coverage {dcov:.0f}% (needs {K.REUSE_MIN_DATA_COVERAGE * 100:.0f}%).")
    db.add(study)
    db.flush()
    return study, "reused"


def apply_reuse_to_property(db: Session, study: CatchmentStudy) -> None:
    """A reused study is already complete: walk the property through the remaining catchment stages with the reason."""
    sync_property(db, study, "CATCHMENT_IN_PROGRESS", "Reusing an existing catchment study")
    sync_property(db, study, "CATCHMENT_COMPLETED", study.reuse_reason or "Reused catchment study")


# ------------------------------------------------------------------ units and completion
def start_unit(db: Session, unit: SurveyWorkUnit) -> None:
    now = datetime.now(timezone.utc)
    if unit.status == "ASSIGNED":
        unit.status, unit.started_at = "IN_PROGRESS", now
    study = unit.study
    if study.status == "REQUESTED":
        study.status, study.started_at = "IN_PROGRESS", now
        sync_property(db, study, "CATCHMENT_IN_PROGRESS", "Survey started")


def aggregate_for_study(db: Session, study: CatchmentStudy) -> dict:
    units = db.scalars(select(SurveyWorkUnit).where(SurveyWorkUnit.catchment_study_id == study.id)).all()
    caps = []
    for u in units:
        for c in u.captures:
            pt = to_shape(c.location)
            caps.append({"type": c.capture_type, "data": c.data, "lat": pt.y, "lon": pt.x})
    prop_point = None
    if study.property_id is not None:
        prop = db.get(Property, study.property_id)
        if prop is not None:
            p = to_shape(prop.location)
            prop_point = (p.x, p.y)
    return catchment_insights.aggregate(
        caps, [{"target": u.target_capture_count, "completed": u.completed_capture_count} for u in units], prop_point)


def new_insight_version(db: Session, study: CatchmentStudy, persona_id: str) -> CatchmentInsight:
    agg = aggregate_for_study(db, study)
    version = (db.scalar(select(func.max(CatchmentInsight.version)).where(
        CatchmentInsight.catchment_study_id == study.id)) or 0) + 1
    ins = CatchmentInsight(catchment_study_id=study.id, version=version, generated_by=persona_id, **agg_columns(agg))
    db.add(ins)
    return ins


def agg_columns(agg: dict) -> dict:
    return {k: agg[k] for k in ("coverage_percentage", "residential_summary", "commercial_summary",
                                "competition_summary", "traffic_summary", "accessibility_summary",
                                "demand_generator_summary", "key_findings", "risks", "overall_ground_fit_score",
                                "data_quality_flags")}


def complete_study(db: Session, study: CatchmentStudy, persona_id: str) -> CatchmentInsight:
    if study.status == "COMPLETED":
        raise StudyError("This study is already completed.")
    units = db.scalars(select(SurveyWorkUnit).where(SurveyWorkUnit.catchment_study_id == study.id)).all()
    if not units:
        raise StudyError("No work units exist yet. Split and assign the catchment first.")
    open_units = [u.unit_code for u in units if u.status != "COMPLETED"]
    if open_units:
        raise StudyError("These work units are not completed yet: " + ", ".join(open_units))
    ins = new_insight_version(db, study, persona_id)
    now = datetime.now(timezone.utc)
    study.status, study.completed_at = "COMPLETED", now
    if study.started_at is None:
        study.started_at = now
    db.flush()
    sync_property(db, study, "CATCHMENT_COMPLETED", "Catchment survey completed and sent to the BD Manager")
    return ins


# ------------------------------------------------------------------ serialisation
def insight_json(ins: CatchmentInsight | None) -> dict | None:
    if ins is None:
        return None
    return {"insight_id": ins.id, "version": ins.version, "coverage_percentage": ins.coverage_percentage,
            "residential": ins.residential_summary, "commercial": ins.commercial_summary,
            "competition": ins.competition_summary, "traffic": ins.traffic_summary,
            "accessibility": ins.accessibility_summary, "demand_generators": ins.demand_generator_summary,
            "key_findings": ins.key_findings, "risks": ins.risks, "ground_fit_score": ins.overall_ground_fit_score,
            "data_quality_flags": ins.data_quality_flags, "generated_at": ins.generated_at.isoformat(),
            "generated_by": ins.generated_by}


def evidence_photos(db: Session, study: CatchmentStudy, limit: int = 12) -> list[dict]:
    root = data_study(db, study)
    rows = db.execute(
        select(SurveyCapturePhoto, SurveyCapture.capture_type, SurveyWorkUnit.unit_code)
        .join(SurveyCapture, SurveyCapture.id == SurveyCapturePhoto.capture_id)
        .join(SurveyWorkUnit, SurveyWorkUnit.id == SurveyCapture.work_unit_id)
        .where(SurveyWorkUnit.catchment_study_id == root.id)
        .order_by(SurveyCapturePhoto.created_at.desc()).limit(limit)).all()
    out = []
    for ph, ctype, _unit in rows:
        pt = to_shape(ph.location) if ph.location is not None else None
        out.append({"photo_id": ph.id, "url": storage.url_for(ph.storage_key), "photo_type": ph.photo_type,
                    "capture_type": ctype, "lat": pt.y if pt else None, "lon": pt.x if pt else None,
                    "captured_at": ph.captured_at.isoformat()})
    return out


def study_status_json(db: Session, study: CatchmentStudy) -> dict:
    """Catchment-level view only (what the BD Manager sees): status, dates, reuse. No unit information."""
    out = {"study_id": study.id, "status": study.status, "requested_by": study.requested_by,
           "requested_at": study.created_at.isoformat(),
           "started_at": study.started_at.isoformat() if study.started_at else None,
           "completed_at": study.completed_at.isoformat() if study.completed_at else None,
           "target": "property" if study.property_id else "area", "property_id": study.property_id,
           "area_id": study.area_id, "reused": bool(study.reused_from_study_id),
           "reused_from_study_id": study.reused_from_study_id, "reuse_reason": study.reuse_reason,
           "data_quality_flags": study.data_quality_flags or []}
    return out


def property_block(db: Session, prop_id: int) -> dict | None:
    """The catchment block for a property: status always; insights and evidence once COMPLETED."""
    study = db.scalar(select(CatchmentStudy).where(CatchmentStudy.property_id == prop_id)
                      .order_by(CatchmentStudy.created_at.desc()).limit(1))
    if study is None:
        return None
    block = study_status_json(db, study)
    if study.status == "COMPLETED":
        ins = latest_insight(db, study)
        block["insights"] = insight_json(ins)
        block["evidence_photos"] = evidence_photos(db, study)
        root = data_study(db, study)
        block["data_study_id"] = root.id
        versions = db.scalars(select(CatchmentInsight.version).where(
            CatchmentInsight.catchment_study_id == root.id).order_by(CatchmentInsight.version)).all()
        block["insight_versions"] = list(versions)
        if root.completed_at:
            block["survey_age_days"] = (datetime.now(timezone.utc) - root.completed_at).days
    return block
