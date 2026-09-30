"""Read-only tools for the BD Manager's conversational analyst.

The LLM only chooses a tool and its arguments; everything below is ordinary backend code over the existing M1/M2/M3
tables (nothing is copied or stored). Each tool returns compact, JSON-safe verified data plus `sources` for the UI, and
returns `{"found": False, "note": "Insufficient data."}` instead of guessing. Comparisons and rankings are computed here,
so the model only explains numbers it was given.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routes import catchments as catchment_routes
from app.api.routes import properties as prop_routes
from app.core.personas import require_roles
from app.models.db_models import Area
from app.models.property_models import STAGES, Property
from app.models.survey_models import CatchmentStudy
from app.services import catchment_service
from app.services.m1_lookup import latest_completed_report

INSUFFICIENT = "Insufficient data."
STUDY_STATUSES = ("REQUESTED", "IN_PROGRESS", "COMPLETED")
SORTS = ("score", "rent", "recent")


def _missing(detail: str | None = None, **extra) -> dict:
    out = {"found": False, "note": INSUFFICIENT, **extra}
    if detail:
        out["detail"] = detail
    return out


def _src(kind: str, ident: int, label: str, href: str | None) -> dict:
    return {"type": kind, "id": ident, "label": label, "href": href}


def _prop_label(p: Property) -> str:
    return p.address or f"Property #{p.id}"


# ------------------------------------------------------------------ lookups
def _pick_area(db: Session, name: str | None):
    """(area, latest_completed_report) for a name, or ('ambiguous', [names]) / (None, None)."""
    name = (name or "").strip()
    if not name:
        return None, None
    rows = db.scalars(select(Area).where(Area.resolved_name.ilike(f"%{name}%")).order_by(Area.id)).all()
    if not rows:
        return None, None
    exact = [a for a in rows if a.resolved_name.lower() == name.lower()]
    rows = exact or rows
    names = sorted({a.resolved_name for a in rows})
    if len(names) > 1:
        return "ambiguous", names[:6]
    best, best_rep = rows[0], None
    for a in rows:  # the same place can have been analysed more than once: use the one with the newest report
        rep = latest_completed_report(db, a.id)
        if rep is not None and (best_rep is None or rep.created_at > best_rep.created_at):
            best, best_rep = a, rep
    return best, best_rep


def _area_ids(db: Session, name: str) -> list[int] | None:
    """All Area rows matching a name (None when the name matches nothing)."""
    rows = db.scalars(select(Area).where(Area.resolved_name.ilike(f"%{name.strip()}%"))).all()
    return [a.id for a in rows] or None


def _visible_property(db: Session, pid: int, persona: dict) -> Property | None:
    """Same visibility as the manager's property list: drafts still being captured are hidden."""
    p = db.get(Property, pid)
    if p is None or not prop_routes._can_view(p, persona):
        return None
    if p.pipeline_stage == "ASSIGNED" and p.submitted_at is None:
        return None
    return p


def _report_facts(rep) -> dict:
    prof = rep.area_profile or {}
    hotspots = []
    for h in (prof.get("hotspots") or [])[:3]:
        row = {k: h[k] for k in ("hotspot_rank", "rank", "cell_id", "score", "locality", "locality_name") if k in h}
        if isinstance(h.get("why"), list):
            row["why"] = h["why"][:2]  # the two strongest reasons are enough for a chat answer
        hotspots.append(row)
    return {"area_id": rep.area_id, "area_name": rep.area.resolved_name, "report_id": rep.id,
            "overall_score": rep.overall_score, "rating": rep.rating,
            "report_date": rep.created_at.date().isoformat(),
            "factors": [{"key": f["key"], "label": f["label"], "weight": f["weight"], "points": f["points"],
                         "raw": f["raw"], "unit": f["unit"]} for f in (rep.score_breakdown or [])],
            "top_hotspots": hotspots,
            "profile": {k: v for k, v in prof.items() if k not in ("hotspots", "facts")
                        and isinstance(v, (int, float, str)) and len(str(v)) < 200},
            "data_quality_flags": rep.data_quality_flags or []}


# ------------------------------------------------------------------ M1 tools
def search_areas(db: Session, persona: dict, query: str = "") -> dict:
    q = select(Area).order_by(Area.id.desc()).limit(20)
    if (query or "").strip():
        q = select(Area).where(Area.resolved_name.ilike(f"%{query.strip()}%")).order_by(Area.id.desc()).limit(20)
    areas = []
    for a in db.scalars(q):
        rep = latest_completed_report(db, a.id)
        areas.append({"area_id": a.id, "area_name": a.resolved_name, "area_km2": round(a.area_km2, 2),
                      "latest_report_id": rep.id if rep else None, "overall_score": rep.overall_score if rep else None,
                      "rating": rep.rating if rep else None})
    if not areas:
        return _missing(f"No area matches '{query}'.")
    return {"found": True, "areas": areas,
            "sources": [_src("area_report", a["latest_report_id"], a["area_name"], f"#/report/{a['latest_report_id']}")
                        for a in areas if a["latest_report_id"]]}


def get_area_report(db: Session, persona: dict, area: str) -> dict:
    a, rep = _pick_area(db, area)
    if a == "ambiguous":
        return {"found": False, "note": "More than one area matches; ask which one.", "candidates": rep}
    if a is None:
        return _missing(f"No area matches '{area}'.")
    if rep is None:
        return _missing(f"{a.resolved_name} has no completed area report.")
    return {"found": True, **_report_facts(rep),
            "sources": [_src("area_report", rep.id, a.resolved_name, f"#/report/{rep.id}")]}


def compare_areas(db: Session, persona: dict, areas: list[str]) -> dict:
    got, missing, ambiguous, seen = [], [], {}, set()
    for name in areas:
        a, rep = _pick_area(db, name)
        if a == "ambiguous":
            ambiguous[name] = rep
        elif a is None or rep is None:
            missing.append(name)
        elif rep.id not in seen:  # the same area named twice is compared once
            seen.add(rep.id)
            got.append(_report_facts(rep))
    if ambiguous:
        return {"found": False, "note": "More than one area matches; ask which one.", "candidates": ambiguous}
    if len(got) < 2:
        return _missing("Fewer than two of these areas have a completed report.", missing=missing, not_found_names=missing,
                        available=[{k: g[k] for k in ("area_name", "report_id", "overall_score", "rating")} for g in got])
    factors = []
    for f in got[0]["factors"]:
        vals = {}
        for g in got:
            m = next((x for x in g["factors"] if x["key"] == f["key"]), None)
            vals[g["area_name"]] = {"points": m["points"], "raw": m["raw"], "unit": m["unit"]} if m else None
        scored = {n: v["points"] for n, v in vals.items() if v and v["points"] is not None}
        row = {"key": f["key"], "label": f["label"], "weight": f["weight"], "values": vals}
        if len(set(scored.values())) > 1:
            row["leader"] = max(scored, key=scored.get)
        else:
            row["no_leader"] = True  # tied or not scored; omitted key, because a null reads as missing data
        factors.append(row)
    ranked = sorted(got, key=lambda g: g["overall_score"], reverse=True)
    return {"found": True, "areas": [{k: g[k] for k in ("area_id", "area_name", "report_id", "overall_score", "rating",
                                                        "report_date", "top_hotspots")} for g in got],
            "factors": factors, "best_area": ranked[0]["area_name"],
            "score_gap": round(ranked[0]["overall_score"] - ranked[1]["overall_score"], 1), "not_found": missing,
            "sources": [_src("area_report", g["report_id"], g["area_name"], f"#/report/{g['report_id']}") for g in got]}


# ------------------------------------------------------------------ M2 tools
def _property_row(s: dict) -> dict:
    ev = s.get("evaluation") or {}
    return {"property_id": s["property_id"], "address": s["address"], "locality": s["locality"],
            "area_name": s["area_name"], "stage": s["pipeline_stage"], "monthly_rent": s["monthly_rent"],
            "total_area_sqft": s["total_area_sqft"], "rent_per_sqft": s["rent_per_sqft"],
            "overall_score": ev.get("overall_score"), "recommendation": ev.get("recommendation")}


def search_properties(db: Session, persona: dict, area: str | None = None, stage: str | None = None,
                      min_score: float | None = None, sort_by: str | None = None, limit: int = 10) -> dict:
    require_roles(persona, "bd_manager")
    # the route function applies the manager's draft-hiding rule; "in Velachery" matches the area name or the locality
    rows = prop_routes.list_properties(stage=stage, area_id=None, mine=False, db=db, persona=persona)
    if area:
        ids, needle = set(_area_ids(db, area) or []), area.strip().lower()
        rows = [s for s in rows if s["area_id"] in ids or needle in (s["locality"] or "").lower()
                or needle in (s["area_name"] or "").lower()]
    rows = [_property_row(s) for s in rows]
    if min_score is not None:
        rows = [r for r in rows if r["overall_score"] is not None and r["overall_score"] >= min_score]
    if sort_by == "score":
        rows.sort(key=lambda r: (r["overall_score"] is None, -(r["overall_score"] or 0)))
    elif sort_by == "rent":
        rows.sort(key=lambda r: (r["monthly_rent"] is None, r["monthly_rent"] or 0))
    if not rows:
        return _missing("No properties match.")
    limit = max(1, min(int(limit or 10), 20))
    shown = rows[:limit]
    return {"found": True, "total_matching": len(rows), "shown": len(shown), "properties": shown,
            "sources": [_src("property", r["property_id"], r["address"] or f"Property #{r['property_id']}",
                             f"#/property/{r['property_id']}") for r in shown]}


_DETAIL_FIELDS = ("frontage_ft", "road_width_ft", "property_type", "floor", "security_deposit", "lease_duration_months",
                  "rent_negotiable", "is_corner_property", "is_main_road_frontage", "traffic_signal_nearby",
                  "visibility_score", "two_wheeler_parking", "four_wheeler_parking", "parking_capacity",
                  "expected_monthly_revenue")


def get_property(db: Session, persona: dict, property_id: int) -> dict:
    require_roles(persona, "bd_manager")
    p = _visible_property(db, property_id, persona)
    if p is None:
        return _missing(f"No submitted property with id {property_id}.")
    out = _property_row(prop_routes.summary_json(db, p))
    for f in _DETAIL_FIELDS:
        v = getattr(p, f)
        out[f] = float(v) if f in ("security_deposit", "expected_monthly_revenue") and v is not None else v
    block = catchment_service.property_block(db, p.id)
    out["catchment_status"] = block["status"] if block else None
    return {"found": True, **out, "sources": [_src("property", p.id, _prop_label(p), f"#/property/{p.id}")]}


def _evaluation_facts(db: Session, p: Property) -> dict | None:
    _latest, done, _prev, rows = prop_routes._latest_evals(db, p.id)
    if done is None:
        return None
    m1 = done.m1_context or {}
    return {"property_id": p.id, "address": _prop_label(p), "area_name": p.area.resolved_name,
            "version": done.evaluation_version, "trigger": done.trigger, "overall_score": done.overall_score,
            "confidence": done.confidence, "recommendation": done.recommendation,
            "factors": [{"key": b["key"], "label": b["label"], "weight": b["weight"],
                         "effective_weight": b.get("effective_weight"), "points": b["points"], "scored": b.get("scored"),
                         "note": b.get("explanation")} for b in (done.score_breakdown or [])],
            "risks": done.risks, "area_score": m1.get("area_score"), "cell_score": m1.get("cell_score"),
            "is_hotspot": m1.get("is_hotspot"), "nearest_savomart_m": m1.get("nearest_savomart_m"),
            "data_quality_flags": done.data_quality_flags or [],
            "versions": [{"version": r.evaluation_version, "trigger": r.trigger, "overall_score": r.overall_score}
                         for r in rows if r.status == "completed"]}


def get_property_evaluation(db: Session, persona: dict, property_id: int) -> dict:
    require_roles(persona, "bd_manager")
    p = _visible_property(db, property_id, persona)
    if p is None:
        return _missing(f"No submitted property with id {property_id}.")
    facts = _evaluation_facts(db, p)
    if facts is None:
        return _missing(f"Property {property_id} has no completed evaluation yet.")
    return {"found": True, **facts, "sources": [_src("property", p.id, _prop_label(p), f"#/property/{p.id}")]}


def compare_properties(db: Session, persona: dict, property_ids: list[int]) -> dict:
    require_roles(persona, "bd_manager")
    got, missing = [], []
    for pid in dict.fromkeys(property_ids):
        p = _visible_property(db, pid, persona)
        facts = _evaluation_facts(db, p) if p else None
        if facts is None:
            missing.append(pid)
            continue
        s = prop_routes.summary_json(db, p)
        facts.update(monthly_rent=s["monthly_rent"], rent_per_sqft=s["rent_per_sqft"], stage=s["pipeline_stage"])
        got.append(facts)
    if len(got) < 2:
        return _missing("Fewer than two of these properties have a completed evaluation.", missing=missing)
    ranked = sorted((g for g in got if g["overall_score"] is not None), key=lambda g: g["overall_score"], reverse=True)
    if len(ranked) < 2:
        return _missing("Fewer than two of these properties have a score.", missing=missing)
    factors = []
    for f in got[0]["factors"]:
        vals = {}
        for g in got:
            m = next((x for x in g["factors"] if x["key"] == f["key"]), None)
            vals[str(g["property_id"])] = m["points"] if m else None
        scored = {k: v for k, v in vals.items() if v is not None}
        row = {"key": f["key"], "label": f["label"], "points_by_property": vals}
        if len(set(scored.values())) > 1:
            row["leader_property_id"] = int(max(scored, key=scored.get))
        else:
            row["no_leader"] = True
        factors.append(row)
    rents = [g["rent_per_sqft"] for g in got if g["rent_per_sqft"] is not None]
    return {"found": True,
            "properties": [{"property_id": g["property_id"], "address": g["address"], "area_name": g["area_name"],
                            "stage": g["stage"], "overall_score": g["overall_score"], "confidence": g["confidence"],
                            "recommendation": g["recommendation"], "monthly_rent": g["monthly_rent"],
                            "rent_per_sqft": g["rent_per_sqft"], "risks": g["risks"]} for g in got],
            "ranking": [{"rank": i + 1, "property_id": g["property_id"], "overall_score": g["overall_score"]}
                        for i, g in enumerate(ranked)],
            "best_property_id": ranked[0]["property_id"],
            "score_gap": round(ranked[0]["overall_score"] - ranked[1]["overall_score"], 1),
            "cheapest_rent_per_sqft_property_id": next((g["property_id"] for g in got if g["rent_per_sqft"] == min(rents)),
                                                       None) if rents else None,
            "factors": factors, "not_found": missing,
            "sources": [_src("property", g["property_id"], g["address"], f"#/property/{g['property_id']}") for g in got]}


# ------------------------------------------------------------------ M3 tools
def _study_facts(db: Session, s: CatchmentStudy, label: str) -> dict:
    out = {**catchment_service.study_status_json(db, s), "label": label}
    out.pop("requested_by", None)
    if s.status == "COMPLETED":
        ins = catchment_service.insight_json(catchment_service.latest_insight(db, s))
        if ins:
            ins.pop("generated_by", None)
            out["insights"] = ins
        root = catchment_service.data_study(db, s)
        if root.completed_at:
            from datetime import datetime, timezone
            out["survey_age_days"] = (datetime.now(timezone.utc) - root.completed_at).days
    return out


def get_catchment(db: Session, persona: dict, property_id: int | None = None, area: str | None = None) -> dict:
    require_roles(persona, "bd_manager")
    if property_id is not None:
        p = _visible_property(db, property_id, persona)
        if p is None:
            return _missing(f"No submitted property with id {property_id}.")
        s = db.scalar(select(CatchmentStudy).where(CatchmentStudy.property_id == p.id)
                      .order_by(CatchmentStudy.created_at.desc()).limit(1))
        if s is None:
            return _missing(f"No catchment study has been requested for property {property_id}.")
        return {"found": True, **_study_facts(db, s, _prop_label(p)),
                "sources": [_src("catchment", s.id, f"Catchment study for {_prop_label(p)}", f"#/property/{p.id}")]}
    a, rep = _pick_area(db, area)
    if a == "ambiguous":
        return {"found": False, "note": "More than one area matches; ask which one.", "candidates": rep}
    if a is None:
        return _missing(f"No area matches '{area}'." if area else "Give a property id or an area name.")
    s = db.scalar(select(CatchmentStudy).where(CatchmentStudy.area_id == a.id)
                  .order_by(CatchmentStudy.created_at.desc()).limit(1))
    if s is None:
        return _missing(f"No area-level catchment study exists for {a.resolved_name}.")
    return {"found": True, **_study_facts(db, s, a.resolved_name),
            "sources": [_src("catchment", s.id, f"Catchment study for {a.resolved_name}",
                             f"#/report/{rep.id}" if rep else None)]}


def search_catchments(db: Session, persona: dict, status: str | None = None, area: str | None = None) -> dict:
    require_roles(persona, "bd_manager")
    ids = None
    if area:
        ids = _area_ids(db, area)
        if not ids:
            return _missing(f"No area matches '{area}'.")
    rows = catchment_routes.list_studies(status=status, db=db, persona=persona)
    out, sources = [], []
    for r in rows:
        prop = db.get(Property, r["property_id"]) if r["property_id"] else None
        area_id = prop.area_id if prop else r["area_id"]
        if ids is not None and area_id not in ids:
            continue
        item = {"study_id": r["study_id"], "target": r["target"], "label": r["label"], "status": r["status"],
                "property_id": r["property_id"], "area_id": area_id, "completed_at": r["completed_at"],
                "reused": r["reused"]}
        if r["status"] == "COMPLETED":
            ins = catchment_service.latest_insight(db, db.get(CatchmentStudy, r["study_id"]))
            item["ground_fit_score"] = ins.overall_ground_fit_score if ins else None
        out.append(item)
    out = out[:20]
    if not out:
        return _missing("No catchment studies match.")
    for i in out:
        href = f"#/property/{i['property_id']}" if i["property_id"] else None
        sources.append(_src("catchment", i["study_id"], i["label"], href))
    return {"found": True, "studies": out, "count": len(out), "sources": sources}


# ------------------------------------------------------------------ registry (also drives argument validation)
# kind: str | int | num | list_str | list_int ; enum limits str values
TOOLS: dict = {
    "search_areas": {"fn": search_areas, "desc": "Find analysed areas by name (empty query lists recent areas) with their latest score.",
                     "args": {"query": ("str", False, None)}},
    "get_area_report": {"fn": get_area_report, "desc": "Latest completed area report for one area: score, factor breakdown, hotspots.",
                        "args": {"area": ("str", True, None)}},
    "compare_areas": {"fn": compare_areas, "desc": "Compare 2 to 4 areas by their latest reports (backend computes the leader per factor and the gap).",
                      "args": {"areas": ("list_str", True, None)}},
    "search_properties": {"fn": search_properties,
                          "desc": "List submitted properties, optionally by area name, pipeline stage, minimum M2 score; sort_by score|rent|recent.",
                          "args": {"area": ("str", False, None), "stage": ("str", False, STAGES),
                                   "min_score": ("num", False, None), "sort_by": ("str", False, SORTS),
                                   "limit": ("int", False, None)}},
    "get_property": {"fn": get_property, "desc": "Basic details of one property (rent, size, stage, latest score).",
                     "args": {"property_id": ("int", True, None)}},
    "get_property_evaluation": {"fn": get_property_evaluation,
                                "desc": "Latest completed M2 evaluation of one property: score, factors, risks, recommendation, versions.",
                                "args": {"property_id": ("int", True, None)}},
    "get_catchment": {"fn": get_catchment,
                      "desc": "Ground catchment survey status and findings for a property (property_id) or an area (area name).",
                      "args": {"property_id": ("int", False, None), "area": ("str", False, None)}},
    "search_catchments": {"fn": search_catchments,
                          "desc": "List catchment studies, optionally by status REQUESTED|IN_PROGRESS|COMPLETED and area name.",
                          "args": {"status": ("str", False, STUDY_STATUSES), "area": ("str", False, None)}},
    "compare_properties": {"fn": compare_properties,
                           "desc": "Compare 2 to 4 properties by their latest M2 evaluations (backend ranks them and computes gaps).",
                           "args": {"property_ids": ("list_int", True, None)}},
}


def validate_args(tool: str, args: dict) -> dict:
    """Checks a tool call coming from the LLM. Raises ValueError on anything unexpected."""
    spec = TOOLS.get(tool)
    if spec is None:
        raise ValueError(f"unknown tool {tool}")
    if not isinstance(args, dict):
        raise ValueError("args must be an object")
    clean: dict = {}
    for name, (kind, required, enum) in spec["args"].items():
        v = args.get(name)
        if v is None or v == "":
            if required:
                raise ValueError(f"{tool}: missing {name}")
            continue
        if kind == "str":
            v = str(v).strip()[:120]
            if enum and v not in enum:
                raise ValueError(f"{tool}: bad {name}")
        elif kind == "int":
            v = int(v)
        elif kind == "num":
            v = float(v)
        elif kind in ("list_str", "list_int"):
            if not isinstance(v, list) or not 2 <= len(v) <= 4:
                raise ValueError(f"{tool}: {name} needs 2 to 4 items")
            v = [str(x).strip()[:120] for x in v] if kind == "list_str" else [int(x) for x in v]
        clean[name] = v
    if tool == "get_catchment" and not ({"property_id", "area"} & clean.keys()):
        raise ValueError("get_catchment: give property_id or area")
    return clean


def run_tool(db: Session, persona: dict, tool: str, args: dict) -> dict:
    clean = validate_args(tool, args)
    return TOOLS[tool]["fn"](db, persona, **clean)
