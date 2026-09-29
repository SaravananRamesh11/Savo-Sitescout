"""M2 tables. They share M1's `Base` so the existing create_all adds them without touching M1 tables.

Properties deliberately carry no grid_cell_id / area_report_id: reports are snapshots, so the exact POINT is the
source of truth and cells/reports are resolved by spatial lookup.
"""
from datetime import date, datetime

from geoalchemy2 import Geometry
from sqlalchemy import (JSON, Boolean, CheckConstraint, Date, DateTime, Float, ForeignKey, Index, Integer, Numeric,
                        SmallInteger, String, Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.db_models import Area, AreaReport, Base, utcnow

STAGES = ["ASSIGNED", "SUBMITTED", "UNDER_REVIEW", "CATCHMENT_REQUESTED", "CATCHMENT_IN_PROGRESS",
          "CATCHMENT_COMPLETED", "FINAL_REVIEW", "APPROVED", "REJECTED"]
_stage_list = ",".join(f"'{s}'" for s in STAGES)


class ScoutingAssignment(Base):
    __tablename__ = "scouting_assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    area_id: Mapped[int] = mapped_column(ForeignKey("areas.id"), index=True)
    source_report_id: Mapped[int | None] = mapped_column(ForeignKey("area_reports.id"), nullable=True)
    hotspot_cell_id: Mapped[str | None] = mapped_column(String(30), nullable=True)
    hotspot_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    hotspot_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    hotspot_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    executive_id: Mapped[str] = mapped_column(String(60), index=True)
    assigned_by: Mapped[str] = mapped_column(String(60))
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="OPEN")  # OPEN | DONE | CANCELLED
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    area: Mapped[Area] = relationship()
    __table_args__ = (CheckConstraint("status in ('OPEN','DONE','CANCELLED')", name="ck_assignment_status"),)


class Property(Base):
    __tablename__ = "properties"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    area_id: Mapped[int] = mapped_column(ForeignKey("areas.id"), index=True)
    assignment_id: Mapped[int | None] = mapped_column(ForeignKey("scouting_assignments.id"), nullable=True)

    location = mapped_column(Geometry("POINT", srid=4326, spatial_index=True), nullable=False)
    location_accuracy_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    location_source: Mapped[str] = mapped_column(String(12), default="gps")  # gps | manual_pin
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_address: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    locality: Mapped[str | None] = mapped_column(String(200), nullable=True)
    pincode: Mapped[str | None] = mapped_column(String(6), nullable=True)

    total_area_sqft: Mapped[float | None] = mapped_column(Float, nullable=True)
    ground_floor_area_sqft: Mapped[float | None] = mapped_column(Float, nullable=True)
    sales_area_sqft: Mapped[float | None] = mapped_column(Float, nullable=True)
    storage_area_sqft: Mapped[float | None] = mapped_column(Float, nullable=True)
    frontage_ft: Mapped[float | None] = mapped_column(Float, nullable=True)
    road_width_ft: Mapped[float | None] = mapped_column(Float, nullable=True)
    floor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    number_of_floors: Mapped[int | None] = mapped_column(Integer, nullable=True)
    property_type: Mapped[str | None] = mapped_column(String(20), nullable=True)

    monthly_rent = mapped_column(Numeric(12, 2), nullable=True)
    security_deposit = mapped_column(Numeric(12, 2), nullable=True)
    lease_duration_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rent_negotiable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    expected_monthly_revenue = mapped_column(Numeric(14, 2), nullable=True)
    revenue_source: Mapped[str | None] = mapped_column(String(120), nullable=True)

    is_corner_property: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_main_road_frontage: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    traffic_signal_nearby: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    signal_distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_access: Mapped[str | None] = mapped_column(String(10), nullable=True)
    exit_access: Mapped[str | None] = mapped_column(String(10), nullable=True)
    visibility_score: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    two_wheeler_parking: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    four_wheeler_parking: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    parking_capacity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parking_type: Mapped[str | None] = mapped_column(String(12), nullable=True)

    duplicate_flags: Mapped[list] = mapped_column(JSON, default=list)
    pipeline_stage: Mapped[str] = mapped_column(String(24), default="ASSIGNED", index=True)
    created_by: Mapped[str] = mapped_column(String(60))
    submitted_by: Mapped[str | None] = mapped_column(String(60), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    area: Mapped[Area] = relationship()
    photos: Mapped[list["PropertyPhoto"]] = relationship(back_populates="property", cascade="all, delete-orphan")
    competitors: Mapped[list["PropertyFieldCompetitor"]] = relationship(cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_properties_area_stage", "area_id", "pipeline_stage"),
        CheckConstraint(f"pipeline_stage in ({_stage_list})", name="ck_property_stage"),
        CheckConstraint("pincode is null or pincode ~ '^[0-9]{6}$'", name="ck_property_pincode"),
        CheckConstraint("location_accuracy_m is null or location_accuracy_m >= 0", name="ck_property_accuracy"),
        CheckConstraint("total_area_sqft is null or total_area_sqft > 0", name="ck_property_total_area"),
        CheckConstraint("ground_floor_area_sqft is null or ground_floor_area_sqft > 0", name="ck_property_ground_area"),
        CheckConstraint("frontage_ft is null or frontage_ft > 0", name="ck_property_frontage"),
        CheckConstraint("road_width_ft is null or road_width_ft > 0", name="ck_property_road_width"),
        CheckConstraint("monthly_rent is null or monthly_rent > 0", name="ck_property_rent"),
        CheckConstraint("expected_monthly_revenue is null or expected_monthly_revenue > 0", name="ck_property_revenue"),
        CheckConstraint("visibility_score is null or visibility_score between 1 and 5", name="ck_property_visibility"),
        CheckConstraint("entry_access is null or entry_access in ('easy','moderate','difficult')", name="ck_property_entry"),
        CheckConstraint("exit_access is null or exit_access in ('easy','moderate','difficult')", name="ck_property_exit"),
    )


class PropertyPhoto(Base):
    __tablename__ = "property_photos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id", ondelete="CASCADE"), index=True)
    photo_type: Mapped[str] = mapped_column(String(24))
    storage_key: Mapped[str] = mapped_column(String(300), unique=True)
    content_type: Mapped[str] = mapped_column(String(40))
    size_bytes: Mapped[int] = mapped_column(Integer)
    uploaded_by: Mapped[str] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    property: Mapped[Property] = relationship(back_populates="photos")
    __table_args__ = (CheckConstraint(
        "photo_type in ('front_view','road_view','side_view','parking_view','interior_view','building_condition')",
        name="ck_photo_type"),)


class PropertyFieldCompetitor(Base):
    __tablename__ = "property_field_competitors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20))  # supermarket|organised_grocery|convenience|kirana|other
    approx_distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PropertyEvaluation(Base):
    """Append-only, versioned. M3 adds new versions rather than editing old ones."""

    __tablename__ = "property_evaluations"
    __table_args__ = (UniqueConstraint("property_id", "evaluation_version", name="uq_eval_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id", ondelete="CASCADE"), index=True)
    evaluation_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(12), default="completed")  # running | completed | failed
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    overall_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_breakdown: Mapped[list | None] = mapped_column(JSON, nullable=True)
    insights: Mapped[list | None] = mapped_column(JSON, nullable=True)
    risks: Mapped[list | None] = mapped_column(JSON, nullable=True)
    recommendation: Mapped[str | None] = mapped_column(String(24), nullable=True)
    explanation: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    explanation_source: Mapped[str | None] = mapped_column(String(12), nullable=True)
    data_sources: Mapped[list | None] = mapped_column(JSON, nullable=True)
    data_quality_flags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    m1_context: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    metrics: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # derived numbers shown on the review screen
    trigger: Mapped[str] = mapped_column(String(24))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_by: Mapped[str] = mapped_column(String(60))


class PropertyStatusHistory(Base):
    __tablename__ = "property_status_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id", ondelete="CASCADE"), index=True)
    from_stage: Mapped[str | None] = mapped_column(String(24), nullable=True)
    to_stage: Mapped[str] = mapped_column(String(24))
    changed_by: Mapped[str] = mapped_column(String(60))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # the evaluation version the manager saw when deciding (final approve/reject): kept for the audit trail
    evaluation_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


def ensure_m2_schema(engine) -> None:
    """create_all never alters existing tables, so additive M2 columns are applied here (idempotent)."""
    from sqlalchemy import text

    with engine.begin() as c:
        c.execute(text("ALTER TABLE property_status_history ADD COLUMN IF NOT EXISTS evaluation_id integer"))
