"""Insights, facts payload, template explanation and the grounded LLM explanation for a property.

The LLM only phrases numbers the backend already computed; grounding.find_ungrounded rejects anything else,
with one retry and then the deterministic template (same pattern as M1's report_text.py).
"""
import json

from app.core import property_scoring_constants as K
from app.services import grounding, llm
from app.services.report_text import _parse_json

SYSTEM_PROMPT = """You are an analyst writing a short property evaluation summary for a grocery-expansion Business
Development Manager at Savomart (neighbourhood grocery stores in Chennai).

STRICT RULES
- Use ONLY numbers that appear in the FACTS JSON. Never compute, estimate, round differently, or invent any figure.
- Do not add outside knowledge (no prices, landmarks, customer counts, revenue).
- Nearby schools, colleges and hospitals are demand/trip-generator signals, NOT guaranteed customers. A traffic
  signal is neither good nor bad by itself. Zero competition is not automatically an opportunity.
- If a factor is unscored ("Insufficient data"), say so; never guess its value.
- If data_quality_flags contains population_mocked or similar, mention the caveat.
- Reference factors by their `key` and risks by their `code`. If FACTS has a `catchment` block (ground survey done by
  people on site), you may cite it with the factor key `ground_survey`; it is context for the decision and does NOT
  change the score.
Return ONLY a JSON object:
{"summary": "2-3 sentences: recommendation and the main reasons",
 "reasons": [{"factor": "<factor key>", "text": "one sentence"}],
 "risk_notes": [{"code": "<risk code>", "text": "one sentence"}],
 "caveats": ["short data-quality caveats, may be empty"]}"""


def build_insights(x: dict, result: dict, metrics: dict) -> list[dict]:
    """Plain-language observations, each backed by a factor or metric (no new numbers)."""
    ins = []
    b = {f["key"]: f for f in result["breakdown"]}
    for key in ("accessibility", "demand_generators", "competition", "space_utilisation", "parking_access",
                "demographic_fit", "area_fitness"):
        f = b[key]
        if f["scored"]:
            tone = "positive" if f["norm"] >= 0.7 else ("negative" if f["norm"] < 0.4 else "neutral")
            ins.append({"factor": key, "tone": tone, "text": f"{f['label']}: {f['explanation']}."})
        else:
            ins.append({"factor": key, "tone": "unknown", "text": f"{f['label']}: {f['explanation']}"})
    m = metrics
    if m.get("rent_per_sqft") is not None:
        ins.insert(0, {"factor": "rent_affordability", "tone": "neutral",
                       "text": f"Rent is Rs {m['rent_per_sqft']:,.0f} per sq ft per month."
                               + (" " + b["rent_affordability"]["explanation"] + "." if b["rent_affordability"]["scored"]
                                  else " Rent affordability is not scored because no expected revenue was provided.")})
    return ins


def build_facts(property_id: int, x: dict, result: dict, metrics: dict, risks: list[dict], rec: str,
                flags: list[str]) -> dict:
    keep = ("key", "label", "weight", "effective_weight", "scored", "points", "raw", "explanation")
    return {
        "property_id": property_id,
        "overall": {"max_score": 100, "score": result["total"], "confidence_share": round(result["confidence"] * 100),
                    "recommendation": rec, "reason": metrics.get("recommendation_reason")},
        "factors": [{k: f[k] for k in keep} for f in result["breakdown"]],
        "metrics": {k: v for k, v in metrics.items() if k not in ("poi", "demographics", "field_competitors",
                                                                   "recommendation_reason", "catchment")},
        "catchment": metrics.get("catchment"),
        "nearby": (x.get("poi") or {}),
        "demographics": x.get("demo"),
        "m1": {k: (x.get("m1") or {}).get(k) for k in ("area_score", "cell_score", "is_hotspot", "hotspot_rank",
                                                        "nearest_savomart", "nearest_savomart_m")},
        "risks": [{"code": r["code"], "severity": r["severity"], "text": r["text"]} for r in risks],
        "assumptions": {"rent_to_revenue_target_pct": [K.RENT_TO_REVENUE_TARGET_MIN * 100,
                                                       K.RENT_TO_REVENUE_TARGET_MAX * 100]},
        "data_quality_flags": flags,
    }


def template_explanation(facts: dict) -> dict:
    ov = facts["overall"]
    scored = sorted([f for f in facts["factors"] if f["scored"]], key=lambda f: f["points"] / max(f["effective_weight"], 1e-9),
                    reverse=True)
    unscored = [f["key"] for f in facts["factors"] if not f["scored"]]
    score_txt = f"scores {ov['score']}/100" if ov["score"] is not None else "could not be scored"
    summary = (f"This property {score_txt} with {ov['confidence_share']}% of the evaluation weight assessed. "
               f"Recommendation: {ov['recommendation'].replace('_', ' ').lower()}. {ov['reason']}")
    reasons = [{"factor": f["key"], "text": f"{f['label']}: {f['explanation']}."} for f in scored[:3]]
    weak = [f for f in scored[::-1][:2] if f["points"] / max(f["effective_weight"], 1e-9) < 0.5]
    reasons += [{"factor": f["key"], "text": f"Weak spot - {f['label'].lower()}: {f['explanation']}."} for f in weak]
    reasons += [{"factor": k, "text": "Insufficient data, so this factor was not scored."} for k in unscored[:3]]
    cat = facts.get("catchment")
    if cat:
        top = " ".join(cat["key_findings"][:3])
        reasons.append({"factor": "ground_survey",
                        "text": f"Ground survey (independent of the score): {top}"})
        for t in cat["risks"][:2]:
            reasons.append({"factor": "ground_survey", "text": f"Ground-survey risk: {t}"})
    notes = [{"code": r["code"], "text": r["text"]} for r in facts["risks"][:5]]
    return {"summary": summary, "reasons": reasons, "risk_notes": notes,
            "caveats": [c for c in facts["data_quality_flags"]]}


def _validate(out: dict, facts: dict) -> list[str]:
    if not isinstance(out, dict) or not isinstance(out.get("summary"), str) or not out["summary"].strip():
        return ["missing summary"]
    keys = {f["key"] for f in facts["factors"]} | ({"ground_survey"} if facts.get("catchment") else set())
    codes = {r["code"] for r in facts["risks"]}
    problems, texts = [], [out["summary"]]
    for r in out.get("reasons") or []:
        if r.get("factor") not in keys:
            problems.append(f"unknown factor {r.get('factor')}")
        texts.append(str(r.get("text", "")))
    for r in out.get("risk_notes") or []:
        if r.get("code") not in codes:
            problems.append(f"unknown risk {r.get('code')}")
        texts.append(str(r.get("text", "")))
    texts += [str(c) for c in out.get("caveats") or []]
    bad = grounding.find_ungrounded(texts, facts)
    if bad:
        problems.append(f"numbers not in facts: {bad}")
    return problems


def generate_explanation(facts: dict) -> tuple[dict, str, list[str]]:
    notes: list[str] = []
    if not llm.is_configured():
        return template_explanation(facts), "template", ["llm_not_configured"]
    prompt = "FACTS:\n" + json.dumps(facts, ensure_ascii=False)
    problems: list[str] = []
    for attempt in range(2):
        try:
            user = prompt if not problems else (prompt + "\n\nYour previous answer was rejected: "
                                                + "; ".join(problems) + ". Use ONLY numbers in FACTS.")
            out = _parse_json(llm.complete(SYSTEM_PROMPT, user))
            problems = _validate(out, facts)
            if not problems:
                return out, "llm", notes
            notes.append(f"llm_attempt_{attempt + 1}_rejected: {'; '.join(problems)}")
        except (llm.LLMUnavailable, ValueError, KeyError) as exc:
            notes.append(f"llm_attempt_{attempt + 1}_failed: {exc}")
            if isinstance(exc, llm.LLMUnavailable):
                break
            problems = ["invalid JSON"]
    return template_explanation(facts), "template", notes + ["llm_output_rejected_template_used"]
