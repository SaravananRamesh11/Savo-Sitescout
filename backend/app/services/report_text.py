"""Facts payload, deterministic template explanation, and the grounded LLM explanation."""
import json

from app.core import config
from app.core import scoring_constants as K
from app.services import grounding, llm

SYSTEM_PROMPT = """You are an analyst writing a short Area Fitness explanation for a grocery-expansion Business
Development Manager at Savomart (neighbourhood grocery stores in Chennai).

STRICT RULES
- Use ONLY the numbers present in the FACTS JSON. Never compute, estimate, round differently, or invent any figure.
  If a number is not in FACTS, do not mention it.
- Do not add outside knowledge about the area (no landmarks, prices, rents, history).
- Population, household and growth figures are ESTIMATES when data_quality_flags contains population_mocked; say so.
- If data_quality_flags mentions mocked/stale data, include a caveat.
- Reference factors by their `key` and hotspots by their `cell_id`.
Return ONLY a JSON object:
{"summary": "2-3 sentences: overall rating and the main reason",
 "reasons": [{"factor": "<factor key>", "text": "one sentence"}],   // 3-5 items, strongest positives and biggest weaknesses
 "scout_first": [{"cell_id": "<cell id>", "text": "one sentence why scout here first"}],
 "caveats": ["short data-quality caveats, may be empty"]}"""


def build_facts(area: dict, profile: dict, area_score: dict, hotspots: list[dict], flags: list[str]) -> dict:
    return {
        "area": area,
        "constants": {"cell_size_m": K_CELL, "competition_radius_m": K.COMPETITION_RADIUS_M,
                      "transit_radius_m": K.TRANSIT_RADIUS_M, "max_score": 100},
        "overall": {"score": area_score["total"], "rating": area_score["rating"]},
        "factors": [{k: f[k] for k in ("key", "label", "group", "weight", "points", "raw", "unit", "explanation")}
                    for f in area_score["breakdown"]],
        "profile": profile,
        "hotspots": [{"rank": h["rank"], "cell_id": h["cell_id"], "score": h["score"], "locality": h["locality"],
                      "nearest_named_road": h["nearest_named_road"], "why": h["why"]} for h in hotspots],
        "data_quality_flags": flags,
    }


K_CELL = config.CELL_SIZE_M

FLAG_TEXT = {
    "population_mocked": "Population, household and growth figures are estimates, not official Census data.",
    "osm_mocked": "OpenStreetMap was unreachable; amenity, road and competition figures are synthetic placeholders.",
    "osm_stale_cache": "OpenStreetMap was unreachable; an older cached copy was used.",
    "savomart_mocked": "The Savomart stores API was unavailable; a saved snapshot of stores was used.",
    "savomart_stale": "The Savomart stores API was unavailable; previously saved stores were used.",
    "area_boundary_approximate": "The area boundary is approximate.",
    "llm_output_rejected": "AI wording failed the number check, so a fixed template was used.",
}


def template_explanation(facts: dict) -> dict:
    """Deterministic explanation: same shape as the LLM output, built only from computed values."""
    f = sorted(facts["factors"], key=lambda x: x["points"] / x["weight"], reverse=True)
    strengths, weak = f[:3], [x for x in f[::-1][:2] if x["points"] / x["weight"] < 0.6]
    a, ov = facts["area"], facts["overall"]
    mocked = any("mock" in x for x in facts["data_quality_flags"])
    provisional = ("PROVISIONAL: OpenStreetMap data was unavailable, so amenity, road and competition figures are "
                   "synthetic placeholders. Re-run the analysis before relying on it. "
                   if "osm_mocked" in facts["data_quality_flags"] else "")
    summary = (provisional + f"{a['name']} scores {ov['score']}/100 ({ov['rating']}) for a Savomart neighbourhood store. "
               f"Strongest signal: {strengths[0]['label'].lower()} ({strengths[0]['explanation']}).")
    reasons = [{"factor": x["key"], "text": f"{x['label']}: {x['explanation']} ({x['points']}/{x['weight']} pts)."}
               for x in strengths]
    reasons += [{"factor": x["key"], "text": f"Weak spot - {x['label'].lower()}: {x['explanation']} "
                                             f"({x['points']}/{x['weight']} pts)."} for x in weak]
    scout = [{"cell_id": h["cell_id"],
              "text": f"#{h['rank']} {h['locality']}"
                      + (f" near {h['nearest_named_road']}" if h["nearest_named_road"] else "")
                      + f" scores {h['score']}/100. " + (h["why"][0] if h["why"] else "")}
             for h in facts["hotspots"]]
    caveats = [FLAG_TEXT.get(c, c) for c in facts["data_quality_flags"]]
    return {"summary": summary, "reasons": reasons, "scout_first": scout, "caveats": caveats}


def _validate(out: dict, facts: dict) -> list[str]:
    """Returns a list of problems (empty == valid and fully grounded)."""
    problems: list[str] = []
    if not isinstance(out, dict) or not isinstance(out.get("summary"), str) or not out["summary"].strip():
        return ["missing summary"]
    keys = {f["key"] for f in facts["factors"]}
    cells = {h["cell_id"] for h in facts["hotspots"]}
    texts = [out["summary"]]
    for r in out.get("reasons") or []:
        if r.get("factor") not in keys:
            problems.append(f"unknown factor {r.get('factor')}")
        texts.append(str(r.get("text", "")))
    for s in out.get("scout_first") or []:
        if s.get("cell_id") not in cells:
            problems.append(f"unknown cell {s.get('cell_id')}")
        texts.append(str(s.get("text", "")))
    texts += [str(c) for c in out.get("caveats") or []]
    bad = grounding.find_ungrounded(texts, facts)
    if bad:
        problems.append(f"numbers not in facts: {bad}")
    return problems


def _parse_json(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw[raw.find("{"):]
    start, end = raw.find("{"), raw.rfind("}")
    return json.loads(raw[start:end + 1])


def generate_explanation(facts: dict) -> tuple[dict, str, list[str]]:
    """Returns (explanation, source, notes). source = 'llm' | 'template'."""
    notes: list[str] = []
    if not llm.is_configured():
        return template_explanation(facts), "template", ["llm_not_configured"]
    prompt = "FACTS:\n" + json.dumps(facts, ensure_ascii=False)
    problems: list[str] = []
    for attempt in range(2):
        try:
            user = prompt if not problems else (
                prompt + "\n\nYour previous answer was rejected: " + "; ".join(problems)
                + ". Use ONLY numbers that appear in FACTS.")
            out = _parse_json(llm.complete(SYSTEM_PROMPT, user))
            problems = _validate(out, facts)
            if not problems:
                return out, "llm", notes
            notes.append(f"llm_attempt_{attempt + 1}_rejected: {'; '.join(problems)}")
        except (llm.LLMUnavailable, ValueError, KeyError) as exc:
            notes.append(f"llm_attempt_{attempt + 1}_failed: {exc}")
            problems = ["invalid JSON"] if isinstance(exc, ValueError) else problems
            if isinstance(exc, llm.LLMUnavailable):
                break
    return template_explanation(facts), "template", notes + ["llm_output_rejected_template_used"]
