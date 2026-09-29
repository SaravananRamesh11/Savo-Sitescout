from datetime import datetime, timezone

from geoalchemy2 import Geometry
from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Area(Base):
    """A resolved, persistent area identity. M2 properties / M3 studies foreign-key to this."""

    __tablename__ = "areas"
    __table_args__ = (UniqueConstraint("input_type", "raw_input", name="uq_area_input"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    input_type: Mapped[str] = mapped_column(String(20))  # pincode | name | grid_cells
    raw_input: Mapped[str] = mapped_column(Text)  # normalised input (pincode, name, sorted cell ids)
    resolved_name: Mapped[str] = mapped_column(String(300))
    geometry = mapped_column(Geometry("POLYGON", srid=4326, spatial_index=True))
    area_km2: Mapped[float] = mapped_column(Float)
    boundary_quality: Mapped[str] = mapped_column(String(40), default="exact")  # exact | approximate
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AreaReport(Base):
    __tablename__ = "area_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    area_id: Mapped[int] = mapped_column(ForeignKey("areas.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|running|completed|failed
    steps: Mapped[list] = mapped_column(JSON, default=list)  # [{node,status,started_at,finished_at,note}]
    failed_node: Mapped[str | None] = mapped_column(String(60), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    overall_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    rating: Mapped[str | None] = mapped_column(String(40), nullable=True)
    score_breakdown: Mapped[list | None] = mapped_column(JSON, nullable=True)
    area_profile: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # descriptive facts
    data_quality_flags: Mapped[list] = mapped_column(JSON, default=list)
    data_sources: Mapped[list] = mapped_column(JSON, default=list)
    llm_explanation: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    explanation_source: Mapped[str | None] = mapped_column(String(40), nullable=True)  # llm | template

    area: Mapped[Area] = relationship()
    grid_cells: Mapped[list["GridCell"]] = relationship(back_populates="report", cascade="all, delete-orphan")


class GridCell(Base):
    __tablename__ = "grid_cells"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("area_reports.id", ondelete="CASCADE"), index=True)
    area_id: Mapped[int] = mapped_column(ForeignKey("areas.id"), index=True)
    cell_id: Mapped[str] = mapped_column(String(30))  # "<col>_<row>" on the fixed UTM-44N grid
    geometry = mapped_column(Geometry("POLYGON", srid=4326, spatial_index=True))
    centroid = mapped_column(Geometry("POINT", srid=4326))
    coverage: Mapped[float] = mapped_column(Float, default=1.0)  # share of the cell inside the area
    score: Mapped[float] = mapped_column(Float)
    score_breakdown: Mapped[list] = mapped_column(JSON)
    features: Mapped[dict] = mapped_column(JSON)
    is_hotspot: Mapped[bool] = mapped_column(Boolean, default=False)
    hotspot_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    locality_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    nearest_named_road: Mapped[str | None] = mapped_column(String(200), nullable=True)
    why: Mapped[list | None] = mapped_column(JSON, nullable=True)

    report: Mapped[AreaReport] = relationship(back_populates="grid_cells")


class SavomartStore(Base):
    __tablename__ = "savomart_stores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_store_id: Mapped[str] = mapped_column(String(60), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    zone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    geometry = mapped_column(Geometry("POINT", srid=4326, spatial_index=True))
    is_operational: Mapped[bool] = mapped_column(Boolean, default=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ExternalDataCache(Base):
    """Exactly one row per (area, source); a successful fetch upserts it."""

    __tablename__ = "external_data_cache"
    __table_args__ = (UniqueConstraint("area_id", "source", name="uq_cache_area_source"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    area_id: Mapped[int] = mapped_column(ForeignKey("areas.id"), index=True)
    source: Mapped[str] = mapped_column(String(30))  # overpass | population
    payload: Mapped[dict] = mapped_column(JSON)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
