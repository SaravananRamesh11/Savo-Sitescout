"""Conversational analyst for the BD Manager: question -> pick tools -> verified data -> explanation.

Flow (one round, no planner): (1) the LLM names up to 3 tools and arguments as JSON, (2) the backend validates and runs
them read-only with the caller's persona, (3) the LLM explains the verified results in prose, and the answer is
rejected if it contains a number that is not in those results (services.grounding). After one retry the answer is
built by a deterministic template from the same data. The model never sees or writes anything except those results.
"""
import json

from sqlalchemy.orm import Session

from app.services import analyst_external  # noqa: F401  (registers the external-data tools)
from app.services import analyst_tools as T
from app.services import llm
from app.services.grounding import find_ungrounded
from app.services.report_text import _parse_json

MAX_CALLS = 3
UNAVAILABLE = "The assistant is unavailable right now. Please try again in a moment."
BUSY = "The assistant is busy (the AI service is rate-limiting requests). Please try again in about a minute."
HELP = ("I can answer questions about your analysed areas, submitted properties and catchment surveys, and look up "
        "Chennai places, pincode areas, nearby amenities (OpenStreetMap) and estimated demographics. For example: "
        "\"What areas are in Chennai?\", \"Compare Velachery and Tambaram\", \"How many supermarkets are near Adyar?\" "
        "or \"Which property has the highest M2 score?\" I do not have official Census tables, Tamil Nadu OGD or Bhuvan data.")

ROUTER_PROMPT = """You route a BD Manager's question to read-only data tools for a retail site-selection app.
Reply with JSON only: {"calls":[{"tool":"<name>","args":{...}}]} with at most 3 calls, or {"calls":[]} if the question
is not about the areas, properties or catchment (ground) surveys.

Tools:
%s

Rules:
- Take area names and property ids from the question or the conversation. Never invent ids.
- "M2 score" and "property score" mean the property evaluation score; "area score" means the area report.
- Highest / best / lowest property: search_properties with sort_by "score" (or "rent") and a small limit.
- Areas with completed catchment studies: search_catchments with status COMPLETED.
- "What areas are in Chennai / which areas can I scout": list_chennai_areas (add query to filter by a name or pincode).
- "Where is X / is X in Chennai / is X analysed": lookup_place.
- Population, households or growth of a place (even one not analysed yet): place_demographics.
- "How many supermarkets / schools / hospitals / banks / bus stops near X": nearby_amenities.
- Prefer the saved-data tools when the manager asks about analysed areas or submitted properties; use the place tools for
  places or facts that saved data cannot answer. Official Census tables, Tamil Nadu OGD and Bhuvan are NOT available:
  return {"calls":[]} for questions that need them.
- You get one round: a tool's result cannot feed another tool's arguments, so do not chain."""

EXPLAIN_PROMPT = """You explain verified data to a BD Manager at a retail chain. Rules:
- Use ONLY the facts in VERIFIED RESULTS. Every number you write must appear there. Do not calculate new numbers; use the
  gaps, leaders and rankings already provided.
- If a result says found=false or a value is null, say "Insufficient data." for that part. Never fill gaps.
- Only if a result has a "candidates" key (several areas share a name), ask which one is meant. Otherwise answer directly;
  when several places match, describe the best match first and mention the others briefly.
- For long lists show at most about 15 names, then say how many more there are.
- If a result has partial=true, only some areas have a saved report: describe the analysed areas' facts and the others'
  ESTIMATES separately, do NOT rank or say which is better, and end with the note's advice.
- Attribute a fact to a source ONLY when that same result names it (a "source" or "basis" field ("place_found_via" only says how the place's position was found, not where the data comes from)). Saved area
  reports, properties and surveys are the manager's own data: no outside attribution. An ESTIMATE is never Census data;
  OpenStreetMap counts may be incomplete, so call them a minimum. If next_step is present, mention it once at the end.
- For a property, overall_score IS its M2 score. For an area, overall_score is its area report (M1) score.
- Refer to properties as "property <id>" with their address, and areas by name. Scores are out of 100.
- A factor marked no_leader is a tie or unscored: say there is no clear leader, not "Insufficient data.".
- Say "Insufficient data." instead of "null" or "none" for values that are missing. Do not mention JSON, tools or field names.
- Write money as rupees with thousands separators (85000.0 -> "₹85,000"), areas as "15,000 sq ft", and turn
  UPPER_SNAKE codes into plain words. Do not show trailing ".0".
- Be concise: at most about 120 words. Plain text only: no markdown, asterisks or tables; use short sentences or
  lines that start with "- " for a list."""


def _tool_lines() -> str:
    out = []
    for name, spec in T.TOOLS.items():
        args = ", ".join(f"{a}{'' if req else '?'}:{k}" + (f"({'|'.join(enum)})" if enum and len(enum) < 10 else "")
                         for a, (k, req, enum) in spec["args"].items())
        out.append(f"- {name}({args}): {spec['desc']}")
    return "\n".join(out)


def _history_text(history: list[dict]) -> str:
    turns = [f"{'Manager' if h['role'] == 'user' else 'Assistant'}: {h['content'][:300]}" for h in history[-4:]]
    return ("CONVERSATION SO FAR:\n" + "\n".join(turns) + "\n\n") if turns else ""


def _route(question: str, history: list[dict]) -> list[dict]:
    """LLM picks tools. Raises llm.LLMUnavailable if the model cannot be reached."""
    system = ROUTER_PROMPT % _tool_lines()
    user = _history_text(history) + "QUESTION: " + question
    last = None
    for _ in range(2):  # one retry if the reply is not valid JSON
        try:
            calls = _parse_json(llm.complete(system, user, max_tokens=400)).get("calls")
            if not isinstance(calls, list):
                raise ValueError("calls must be a list")
            return calls[:MAX_CALLS]
        except (ValueError, KeyError) as exc:
            last = exc
    raise ValueError(f"router reply unusable: {last}")


def _execute(db: Session, persona: dict, calls: list[dict]) -> list[dict]:
    results = []
    for c in calls:
        tool = c.get("tool") if isinstance(c, dict) else None
        try:
            res = T.run_tool(db, persona, tool, c.get("args") or {})
        except (ValueError, TypeError) as exc:  # unknown tool or bad arguments from the model: refuse, do not guess
            res = T._missing(f"The request for {tool} was not valid ({exc}).")
        results.append({"tool": tool, "args": c.get("args") or {}, "result": res})
    return results


# ------------------------------------------------------------------ deterministic answer (fallback, and no-data)
def _n(v) -> str:
    return "insufficient data" if v is None else (f"{v:g}" if isinstance(v, (int, float)) else str(v))


def _render(tool: str, r: dict) -> str:
    if not r.get("found"):
        if r.get("candidates"):
            c = r["candidates"]
            names = ", ".join(sorted(c) if isinstance(c, list) else {n for v in c.values() for n in v})
            return f"More than one area matches ({names}). Which one do you mean?"
        extra = f" ({r['detail']})" if r.get("detail") else ""
        return f"{T.INSUFFICIENT}{extra}"
    if tool == "get_area_report":
        return f"{r['area_name']} scored {_n(r['overall_score'])} ({r['rating']}) in area report {r['report_id']}."
    if tool == "compare_areas" and r.get("partial"):
        done = "; ".join(f"{a['area_name']} scored {_n(a['overall_score'])}" for a in r["analysed_areas"])
        est = "; ".join(f"{x['name']}: {_n(x['estimate']['population_per_km2'])} people/km2 (estimate)"
                        for x in r["not_analysed_areas"] if x["estimate"])
        return f"Partial comparison. Analysed: {done or 'none'}. Not analysed: {est or 'no estimate available'}. " + r["note"]
    if tool == "compare_areas":
        head = ", ".join(f"{a['area_name']} {_n(a['overall_score'])}" for a in r["areas"])
        lead = "; ".join(f"{f['label']}: {f['leader']}" for f in r["factors"] if f.get("leader"))
        return (f"Scores: {head}. {r['best_area']} leads by {_n(r['score_gap'])} points."
                + (f" Factor leaders: {lead}." if lead else ""))
    if tool == "search_areas":
        return "Areas: " + "; ".join(f"{a['area_name']} (score {_n(a['overall_score'])})" for a in r["areas"]) + "."
    if tool == "search_properties":
        lines = [f"property {p['property_id']} {p['address'] or ''} in {p['area_name']}: score {_n(p['overall_score'])}, "
                 f"stage {p['stage']}" for p in r["properties"]]
        return f"{r['total_matching']} matching properties. " + "; ".join(lines) + "."
    if tool == "get_property":
        return (f"Property {r['property_id']} {r['address'] or ''} in {r['area_name']}: stage {r['stage']}, "
                f"score {_n(r['overall_score'])}, rent {_n(r['monthly_rent'])}.")
    if tool == "get_property_evaluation":
        return (f"Property {r['property_id']} scored {_n(r['overall_score'])} (recommendation {_n(r['recommendation'])}, "
                f"confidence {_n(r['confidence'])}).")
    if tool == "compare_properties":
        rk = ", ".join(f"property {x['property_id']} ({_n(x['overall_score'])})" for x in r["ranking"])
        return f"Ranking: {rk}. Property {r['best_property_id']} leads by {_n(r['score_gap'])} points."
    if tool == "get_catchment":
        ins = r.get("insights")
        if not ins:
            return f"The catchment study for {r['label']} is {r['status']}; there are no findings yet."
        findings = " ".join(str(x) for x in (ins.get("key_findings") or [])[:3])
        return f"Catchment study for {r['label']} is completed. Ground-fit score: {_n(ins['ground_fit_score'])}. {findings}"
    if tool == "list_chennai_areas":
        zones = ", ".join(z["name"] for z in r["chennai_corporation_zones"])
        subs = ", ".join(z["name"] for z in r["suburb_areas"])
        more = f" and {r['suburb_areas_not_shown']} more" if r["suburb_areas_not_shown"] else ""
        return (f"You can scout {r['chennai_corporation_zones_total']} Chennai Corporation zones ({zones}) and "
                f"{r['suburb_areas_total']} suburb areas ({subs}{more}), covering {r['pincodes_total']} pincodes. "
                + r["next_step"])
    if tool == "lookup_place":
        p = r["places"][0]
        done = p["saved_areas_containing_it"]
        return (f"{p['name']} ({p['type']}) is at {p['lat']}, {p['lon']} according to OpenStreetMap"
                + (f"; it is inside the analysed area {done[0]['area_name']}." if done else "; it has not been analysed yet."))
    if tool == "place_demographics":
        e = r["estimate"]
        return (f"Estimate near {r['place']} (not Census data): {e['population_per_km2']} people/km2, "
                f"{e['households_per_km2']} households/km2, growth {e['growth_percent_per_year']}% a year.")
    if tool == "nearby_amenities":
        c = ", ".join(f"{v} {k}" for k, v in r["counts_within_radius"].items())
        return f"Within {r['radius_m']} m of {r['place']} OpenStreetMap maps: {c} (a minimum; OSM can be incomplete)."
    if tool == "search_catchments":
        return "Catchment studies: " + "; ".join(f"{s['label']} ({s['status']})" for s in r["studies"]) + "."
    return T.INSUFFICIENT


def _template(results: list[dict]) -> str:
    return "\n".join(_render(x["tool"], x["result"]) for x in results)


def _explain(question: str, results: list[dict]) -> tuple[str, str]:
    facts = {"score_scale": "out of 100 (0-100)", "results": [{"tool": x["tool"], "result": {
        k: v for k, v in x["result"].items() if k != "sources"}} for x in results]}
    prompt = ("QUESTION: " + question + "\n\nVERIFIED RESULTS (JSON):\n" + json.dumps(facts, ensure_ascii=False, default=str))
    problems: list[str] = []
    for _ in range(2):
        user = prompt if not problems else (
            prompt + "\n\nYour previous answer used numbers that are not in VERIFIED RESULTS: " + ", ".join(problems)
            + ". Rewrite it using only numbers that appear there.")
        try:
            text = llm.complete(EXPLAIN_PROMPT, user, max_tokens=700).strip()
        except llm.LLMUnavailable:
            break
        problems = find_ungrounded([text], facts) if text else ["empty answer"]
        if not problems:
            return text, "llm"
    return _template(results), "template"


def _sources(results: list[dict]) -> list[dict]:
    seen, out = set(), []
    for x in results:
        for s in x["result"].get("sources") or []:
            if (s["type"], s["id"]) not in seen:
                seen.add((s["type"], s["id"]))
                out.append(s)
    return out[:8]


def answer(db: Session, persona: dict, question: str, history: list[dict] | None = None) -> dict:
    history = history or []
    base = {"answer": "", "sources": [], "tools_used": [], "mode": "llm", "results": []}
    if not llm.is_configured():
        return {**base, "answer": UNAVAILABLE, "mode": "unavailable"}
    try:
        calls = _route(question, history)
    except llm.LLMBusy:
        return {**base, "answer": BUSY, "mode": "busy"}
    except (llm.LLMUnavailable, ValueError):
        return {**base, "answer": UNAVAILABLE, "mode": "unavailable"}
    if not calls:
        return {**base, "answer": HELP, "mode": "help"}
    results = _execute(db, persona, calls)
    base.update(results=results, tools_used=[{"tool": x["tool"], "args": x["args"]} for x in results])
    if not any(x["result"].get("found") for x in results):  # nothing to explain: say so, deterministically
        return {**base, "answer": _template(results), "mode": "template"}
    text, mode = _explain(question, results)
    return {**base, "answer": text, "mode": mode, "sources": _sources(results)}
