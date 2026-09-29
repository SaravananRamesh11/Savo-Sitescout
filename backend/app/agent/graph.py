"""LangGraph StateGraph wiring the 9 sequential nodes, plus the background runner with progress tracking."""
import traceback
from datetime import datetime, timezone

from langgraph.graph import END, START, StateGraph

from app.agent.nodes.pipeline import NODES
from app.agent.state import AgentState
from app.core.db import get_session
from app.models.db_models import AreaReport


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _set_step(db, report_id, node, **updates):
    report = db.get(AreaReport, report_id)
    steps = [dict(s) for s in report.steps]
    for s in steps:
        if s["node"] == node:
            s.update(updates)
    report.steps = steps
    db.commit()


def build_graph(db):
    """Each node is wrapped so progress is persisted and failures identify the failing node."""

    def wrap(name, fn):
        def run(state: AgentState):
            _set_step(db, state["report_id"], name, status="running", started_at=_now_iso())
            try:
                update = fn(state, db) or {}
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                report = db.get(AreaReport, state["report_id"])
                report.failed_node, report.error = name, f"{type(exc).__name__}: {exc}"
                db.commit()
                _set_step(db, state["report_id"], name, status="failed", finished_at=_now_iso(),
                          note=str(exc)[:300])
                raise
            _set_step(db, state["report_id"], name, status="done", finished_at=_now_iso(),
                      note=str(state["notes"].get(name, "")))
            # nodes mutate flags/sources/notes in place; return them so LangGraph carries them forward
            return {**update, "flags": state["flags"], "sources": state["sources"], "notes": state["notes"]}
        return run

    g = StateGraph(AgentState)
    prev = START
    for name, _label, fn in NODES:
        g.add_node(name, wrap(name, fn))
        g.add_edge(prev, name)
        prev = name
    g.add_edge(prev, END)
    return g.compile()


def initial_steps():
    return [{"node": n, "label": label, "status": "pending", "started_at": None, "finished_at": None, "note": ""}
            for n, label, _ in NODES]


def run_report(report_id: int) -> None:
    """Background task entrypoint (also used for retry: external data is served from cache)."""
    db = get_session()
    try:
        report = db.get(AreaReport, report_id)
        report.status, report.error, report.failed_node = "running", None, None
        report.steps = initial_steps()
        db.commit()
        state: AgentState = {"report_id": report_id, "area_id": report.area_id, "flags": [], "sources": [],
                             "notes": {}}
        build_graph(db).invoke(state)
        report = db.get(AreaReport, report_id)
        report.status, report.completed_at = "completed", datetime.now(timezone.utc)
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        report = db.get(AreaReport, report_id)
        report.status = "failed"
        report.error = report.error or f"{type(exc).__name__}: {exc}"
        db.commit()
        traceback.print_exc()
    finally:
        db.close()
