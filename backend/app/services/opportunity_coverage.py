"""Scouting coverage per 500 m cell (0 = nothing scouted, 1 = fully scouted), from existing M2 / M3 data.

Signals (all constants in core.opportunity_constants):
  * a property captured in the cell (M2): 1.0 unit if it has a completed evaluation, 0.5 if not
  * an OPEN scouting assignment on that hotspot cell: 0.5
  * a catchment study (M3) covering the cell: 3.0 if COMPLETED, 1.5 if requested / in progress
A signal fades with age (full for 90 days, linear to nothing at 365). Point signals count fully in their own cell and 50%
in the 8 neighbours (about 500 m of walking distance); a study counts in every cell its polygon covers. Units add up and
are capped: coverage = min(1, units / 3). A lone or old property therefore lowers a cell's priority a little instead of
excluding it, and a completed catchment study fills it. Everything is read with three queries, then binned in Python.
"""
from datetime import datetime, timezone

from geoalchemy2.shape import to_shape
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core import opportunity_constants as C
from app.models.survey_models import CatchmentStudy
from app.services import grid

_PROPS = text("""
SELECT p.id, ST_X(p.location::geometry) AS lon, ST_Y(p.location::geometry) AS lat,
       COALESCE(p.submitted_at, p.created_at) AS at,
       EXISTS (SELECT 1 FROM property_evaluations e WHERE e.property_id = p.id AND e.status = 'completed') AS evaluated
FROM properties p
""")
_ASSIGN = text("SELECT id, hotspot_cell_id, created_at FROM scouting_assignments "
               "WHERE status = 'OPEN' AND hotspot_cell_id IS NOT NULL")


def recency(age_days: float) -> float:
    """1.0 while recent, fading linearly to 0 at C.EXPIRE_DAYS."""
    if age_days <= C.RECENT_FULL_DAYS:
        return 1.0
    if age_days >= C.EXPIRE_DAYS:
        return 0.0
    return (C.EXPIRE_DAYS - age_days) / (C.EXPIRE_DAYS - C.RECENT_FULL_DAYS)


def _age_days(at: datetime, now: datetime) -> float:
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return max(0.0, (now - at).total_seconds() / 86400)


def combine(signals: list[dict]) -> dict:
    """signals: [{cells: [(col, row)], units, spread: bool, kind, ref, age_days}] -> {cell_id: {coverage, units, signals}}.
    Pure, so the maths can be tested without a database."""
    acc: dict[str, dict] = {}

    def add(cell, units, sig, share):
        cid = grid.make_cell_id(*cell)
        row = acc.setdefault(cid, {"units": 0.0, "signals": []})
        row["units"] += units
        row["signals"].append({"kind": sig["kind"], "ref": sig["ref"], "units": round(units, 3),
                               "age_days": round(sig["age_days"]), "share": share})

    for s in signals:
        eff = s["units"] * recency(s["age_days"])
        if eff <= 0:
            continue
        for cell in s["cells"]:
            add(cell, eff, s, 1.0)
            if s.get("spread"):
                for dc in (-1, 0, 1):
                    for dr in (-1, 0, 1):
                        if dc or dr:
                            add((cell[0] + dc, cell[1] + dr), eff * C.NEIGHBOUR_SHARE, s, C.NEIGHBOUR_SHARE)
    return {cid: {"coverage": round(min(1.0, r["units"] / C.COVERAGE_FULL_UNITS), 3), "units": round(r["units"], 3),
                  "signals": r["signals"]} for cid, r in acc.items()}


def collect_signals(db: Session, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    signals: list[dict] = []
    for r in db.execute(_PROPS):
        x, y = grid.lonlat_to_utm(r.lon, r.lat)
        signals.append({"cells": [grid.cell_of_utm(x, y)], "spread": True, "kind": "property", "ref": r.id,
                        "units": C.UNITS_PROPERTY_EVALUATED if r.evaluated else C.UNITS_PROPERTY_UNEVALUATED,
                        "age_days": _age_days(r.at, now)})
    for r in db.execute(_ASSIGN):
        try:
            cell = grid.parse_cell_id(r.hotspot_cell_id)
        except ValueError:
            continue
        signals.append({"cells": [cell], "spread": True, "kind": "assignment", "ref": r.id,
                        "units": C.UNITS_OPEN_ASSIGNMENT, "age_days": _age_days(r.created_at, now)})
    for s in db.scalars(select(CatchmentStudy)):
        completed = s.status == "COMPLETED"
        when = (s.completed_at if completed and s.completed_at else s.created_at)
        cells = [(c, r) for c, r, _ in grid.cells_for_polygon(grid.to_utm(to_shape(s.study_geometry)), min_coverage=0.2)]
        signals.append({"cells": cells, "spread": False, "kind": "catchment_study" if completed else "catchment_study_active",
                        "ref": s.id, "units": C.UNITS_STUDY_COMPLETED if completed else C.UNITS_STUDY_ACTIVE,
                        "age_days": _age_days(when, now)})
    return signals


def compute_coverage(db: Session, now: datetime | None = None) -> dict[str, dict]:
    return combine(collect_signals(db, now))


def describe(entry: dict | None) -> str:
    """One line for the UI: what the scouting evidence in a cell is."""
    if not entry or entry["coverage"] <= 0:
        return "No recent property scouting or catchment study here."
    names = {"property": "property", "assignment": "open assignment", "catchment_study": "completed catchment study",
             "catchment_study_active": "catchment study in progress"}
    parts: dict[str, int] = {}
    for s in entry["signals"]:
        label = names.get(s["kind"], s["kind"]) + (" nearby" if s["share"] < 1 and s["kind"] != "catchment_study" else "")
        parts[label] = parts.get(label, 0) + 1
    listing = ", ".join(f"{n} {k}" + ("s" if n > 1 else "") for k, n in sorted(parts.items()))
    return f"{entry['coverage'] * 100:.0f}% scouted ({listing})."
