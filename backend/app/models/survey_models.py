"""M3 tables (five). They share M1's `Base`, so create_all adds them without touching M1/M2 tables.

References: catchment_studies -> M2 `properties` (nullable) or M1 `areas` (nullable), exactly one non-null.
There is no User table in this project (seeded personas), so every "user" column is a persona-id string, as in M2.
"""
from datetime import datetime

from geoalchemy2 import Geometry
from sqlalchemy import (JSON, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, SmallInteger, String, Text,
                        UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.db_models import Base, utcnow
from app.models.property_models import Property  # noqa: F401  (registers the FK target)

STUDY_STATUSES = ("REQUESTED", "IN_PROGRESS", "COMPLETED")
UNIT_STATUSES = ("ASSIGNED", "IN_PROGRESS", "COMPLETED")
CAPTURE_TYPES = ("residential", "commercial", "competition", "traffic", "accessibility", "demand_generator",
                 "local_condition")
CAPTURE_PHOTO_TYPES = ("competitor_evidence", "road_condition", "parking", "obstruction", "commercial", "other")


def _in(col: str, values: tuple) -> str:
    return f"{col} in ({','.join(repr(v) for v in values)})"


class CatchmentStudy(Base):
    __tablename__ = "catchment_studies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    property_id: Mapped[int | None] = mapped_column(ForeignKey("properties.id", ondelete="RESTRICT"), nullable=True, index=True)
    area_id: Mapped[int | None] = mapped_column(ForeignKey("areas.id", ondelete="RESTRICT"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(12), default="REQUESTED", index=True)
    study_geometry = mapped_column(Geometry("POLYGON", srid=4326, spatial_index=True), nullable=False)
    requested_by: Mapped[str] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reused_from_study_id: Mapped[int | None] = mapped_column(
        ForeignKey("catchment_studies.id", ondelete="RESTRICT"), nullable=True, index=True)
    reuse_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    data_quality_flags: Mapped[list] = mapped_column(JSON, default=list)

    units: Mapped[list["SurveyWorkUnit"]] = relationship(back_populates="study", cascade="all, delete-orphan")
    insights: Mapped[list["CatchmentInsight"]] = relationship(back_populates="study", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint(_in("status", STUDY_STATUSES), name="ck_study_status"),
        CheckConstraint("(property_id IS NULL) <> (area_id IS NULL)", name="ck_study_exactly_one_target"),
    )


class SurveyWorkUnit(Base):
    __tablename__ = "survey_work_units"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    catchment_study_id: Mapped[int] = mapped_column(ForeignKey("catchment_studies.id", ondelete="CASCADE"), index=True)
    unit_code: Mapped[str] = mapped_column(String(30))
    geometry = mapped_column(Geometry("MULTIPOLYGON", srid=4326, spatial_index=True), nullable=False)
    unit_type: Mapped[str] = mapped_column(String(20), default="lane_zone")
    assigned_to: Mapped[str] = mapped_column(String(60), index=True)
    status: Mapped[str] = mapped_column(String(12), default="ASSIGNED", index=True)
    priority: Mapped[int] = mapped_column(SmallInteger, default=2)
    estimated_distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_capture_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completed_capture_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # tweak (see m3_tweaks.md): the workload estimate the Survey Manager sees before assigning
    workload: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    study: Mapped[CatchmentStudy] = relationship(back_populates="units")
    captures: Mapped[list["SurveyCapture"]] = relationship(back_populates="unit", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("catchment_study_id", "unit_code", name="uq_unit_code_per_study"),
        CheckConstraint(_in("status", UNIT_STATUSES), name="ck_unit_status"),
        CheckConstraint("priority between 1 and 3", name="ck_unit_priority"),
        CheckConstraint("completed_capture_count >= 0", name="ck_unit_capture_count"),
    )


class SurveyCapture(Base):
    __tablename__ = "survey_captures"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    work_unit_id: Mapped[int] = mapped_column(ForeignKey("survey_work_units.id", ondelete="CASCADE"), index=True)
    captured_by: Mapped[str] = mapped_column(String(60))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    location = mapped_column(Geometry("POINT", srid=4326, spatial_index=True), nullable=False)
    location_accuracy_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    capture_type: Mapped[str] = mapped_column(String(20))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    unit: Mapped[SurveyWorkUnit] = relationship(back_populates="captures")
    photos: Mapped[list["SurveyCapturePhoto"]] = relationship(back_populates="capture", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint(_in("capture_type", CAPTURE_TYPES), name="ck_capture_type"),
        CheckConstraint("location_accuracy_m is null or location_accuracy_m >= 0", name="ck_capture_accuracy"),
        Index("ix_capture_unit_type", "work_unit_id", "capture_type"),
    )


class SurveyCapturePhoto(Base):
    __tablename__ = "survey_capture_photos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    capture_id: Mapped[int] = mapped_column(ForeignKey("survey_captures.id", ondelete="CASCADE"), index=True)
    storage_key: Mapped[str] = mapped_column(String(300), unique=True)
    photo_type: Mapped[str] = mapped_column(String(24))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    location = mapped_column(Geometry("POINT", srid=4326), nullable=True)
    location_accuracy_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    capture: Mapped[SurveyCapture] = relationship(back_populates="photos")
    __table_args__ = (CheckConstraint(_in("photo_type", CAPTURE_PHOTO_TYPES), name="ck_capture_photo_type"),)


class CatchmentInsight(Base):
    """Append-only, versioned aggregation of the completed ground survey."""

    __tablename__ = "catchment_insights"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    catchment_study_id: Mapped[int] = mapped_column(ForeignKey("catchment_studies.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    coverage_percentage: Mapped[float] = mapped_column(Float)  # 0-100
    residential_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    commercial_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    competition_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    traffic_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    accessibility_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    demand_generator_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    key_findings: Mapped[list] = mapped_column(JSON, default=list)
    risks: Mapped[list] = mapped_column(JSON, default=list)
    overall_ground_fit_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    data_quality_flags: Mapped[list] = mapped_column(JSON, default=list)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    generated_by: Mapped[str] = mapped_column(String(60))

    study: Mapped[CatchmentStudy] = relationship(back_populates="insights")
    __table_args__ = (
        UniqueConstraint("catchment_study_id", "version", name="uq_insight_version"),
        CheckConstraint("coverage_percentage between 0 and 100", name="ck_insight_coverage"),
    )


def ensure_m3_schema(engine) -> None:
    """create_all creates the five tables. There is no Alembic in this project (M1/M2 use create_all plus idempotent
    additive DDL), so M3 follows the same convention. Nothing to alter yet; kept as the single hook for later columns."""
    Base.metadata.create_all(engine, tables=[CatchmentStudy.__table__, SurveyWorkUnit.__table__,
                                             SurveyCapture.__table__, SurveyCapturePhoto.__table__,
                                             CatchmentInsight.__table__])
