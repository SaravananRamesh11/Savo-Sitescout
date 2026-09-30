"""Opportunity Finder tables. Additive only: no existing table is touched (create_all builds them on startup).

* opportunity_tiles: per 6 km tile, the *derived* per-cell features and place names computed from one Overpass fetch,
  with when and where it came from. Raw road geometry is not stored (tens of MB); features + source/fetched time are.
* opportunity_runs: one row per "Find Opportunities" click: progress, the assumptions used, per-cell results, the top
  results with their details, data sources and quality flags. Only the latest few runs are kept.
"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models.db_models import Base, utcnow


class OpportunityTile(Base):
    __tablename__ = "opportunity_tiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tile_key: Mapped[str] = mapped_column(String(30))  # "<tile_col>_<tile_row>" on the fixed lattice
    payload: Mapped[dict] = mapped_column(JSON)  # {"cells": {cell_id: features}, "places": [...], "counts": {...}}
    endpoint: Mapped[str | None] = mapped_column(String(200), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (UniqueConstraint("tile_key", name="uq_opportunity_tile_key"),)


class OpportunityRun(Base):
    __tablename__ = "opportunity_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(12), default="queued")  # queued | running | completed | failed
    created_by: Mapped[str] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    progress: Mapped[dict] = mapped_column(JSON, default=dict)  # {phase, done, total, message}
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    cells: Mapped[list] = mapped_column(JSON, default=list)  # compact per-cell records
    top: Mapped[list] = mapped_column(JSON, default=list)  # detailed top results
    summary: Mapped[dict] = mapped_column(JSON, default=dict)  # counts, city average breakdown, stores
    data_quality_flags: Mapped[list] = mapped_column(JSON, default=list)
    data_sources: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
