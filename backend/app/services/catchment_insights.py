"""Aggregates the real survey captures into catchment insights. Pure: no I/O, no invented values.

Rules:
* Summaries are counts and distributions of what executives actually recorded.
* A rated category (residential, commercial, traffic, accessibility) needs MIN_CAPTURES_PER_CATEGORY observations,
  otherwise it is reported as "Insufficient data". Presence categories (competition, demand generators) are
  meaningful only when the survey covered enough ground, otherwise "no competitors recorded" would prove nothing.
* key_findings and risks are rule-generated sentences that quote only computed values.
* overall_ground_fit_score is an independent 0-100 indicator over categories that have enough data. It is NULL when
  fewer than MIN_CATEGORIES_FOR_GROUND_SCORE categories are sufficient, and it never feeds the M2 property score.
"""
from __future__ import annotations

import statistics
from collections import Counter

from shapely.geometry import Point

from app.core import survey_constants as K
from app.services import grid

RATED = ("residential", "commercial", "traffic", "accessibility")
PRESENCE = ("competition", "demand_generator")


def _dist(values) -> dict:
    c = Counter(v for v in values if v)
    return dict(c)


def _sum(caps, key) -> int | None:
    vals = [c["data"][key] for c in caps if c["data"].get(key) is not None]
    return sum(vals) if vals else None


def _top(counter: dict) -> str | None:
    return max(counter, key=counter.get) if counter else None


def coverage(units: list[dict]) -> float:
    """0-100: captured / target across units (each unit capped at its target)."""
    target = sum(u.get("target") or 0 for u in units)
    if target <= 0:
        return 0.0
    got = sum(min(u.get("completed") or 0, u.get("target") or 0) for u in units)
    return round(min(100.0, got / target * 100), 1)


def _competitors(caps, prop_point) -> list[dict]:
    """Observed competitors, de-duplicated: same name + type within COMPETITOR_DEDUPE_M is one competitor."""
    kept: list[dict] = []
    for c in caps:
        d = c["data"]
        name = (d.get("name") or "").strip().lower()
        x, y = grid.lonlat_to_utm(c["lon"], c["lat"])
        dup = False
        for k in kept:
            if k["_name"] == name and k["competitor_type"] == d["competitor_type"] and \
                    ((k["_x"] - x) ** 2 + (k["_y"] - y) ** 2) ** 0.5 <= K.COMPETITOR_DEDUPE_M:
                dup = True
                break
        if dup:
            continue
        dist = None
        if prop_point is not None:
            px, py = grid.lonlat_to_utm(prop_point[0], prop_point[1])
            dist = round(((px - x) ** 2 + (py - y) ** 2) ** 0.5)
        kept.append({"_name": name, "_x": x, "_y": y, "name": d.get("name") or "unnamed",
                     "competitor_type": d["competitor_type"], "size": d["size"],
                     "customer_activity": d["customer_activity"], "lat": round(c["lat"], 6),
                     "lon": round(c["lon"], 6), "distance_to_property_m": dist})
    for k in kept:
        for hidden in ("_name", "_x", "_y"):
            k.pop(hidden)
    return sorted(kept, key=lambda k: (k["distance_to_property_m"] is None, k["distance_to_property_m"] or 0))


def _level_score(dist: dict) -> float | None:
    n = sum(dist.values())
    if not n:
        return None
    return sum(K.LEVEL_VALUE[k] * v for k, v in dist.items() if k in K.LEVEL_VALUE) / n


def aggregate(captures: list[dict], units: list[dict], prop_point: tuple[float, float] | None = None) -> dict:
    """captures: [{type, data, lat, lon}], units: [{target, completed}], prop_point: (lon, lat) or None."""
    by = {t: [c for c in captures if c["type"] == t] for t in
          ("residential", "commercial", "competition", "traffic", "accessibility", "demand_generator", "local_condition")}
    cov = coverage(units)
    enough_cov = cov / 100 >= K.REUSE_MIN_DATA_COVERAGE
    flags: list[str] = []

    def rated_ok(t):
        return len(by[t]) >= K.MIN_CAPTURES_PER_CATEGORY

    # ---- residential
    res = by["residential"]
    residential = {"observations": len(res), "sufficient": rated_ok("residential")}
    if res:
        residential.update({
            "buildings_visible": _sum(res, "buildings_visible"), "independent_houses": _sum(res, "independent_houses"),
            "apartment_complexes": _sum(res, "apartment_complexes"),
            "activity_level": _dist(c["data"]["activity_level"] for c in res),
            "occupancy": _dist(c["data"].get("occupancy") for c in res),
            "construction_activity": _dist(c["data"].get("construction_activity") for c in res)})
    if not residential["sufficient"]:
        residential["status"] = "Insufficient data"

    # ---- commercial
    com = by["commercial"]
    kinds: Counter = Counter()
    for c in com:
        kinds[c["data"]["business_kind"]] += c["data"].get("count", 1)
    commercial = {"observations": len(com), "sufficient": rated_ok("commercial")}
    if com:
        commercial.update({"businesses_by_kind": dict(kinds), "businesses_total": sum(kinds.values()),
                           "activity_level": _dist(c["data"]["activity_level"] for c in com),
                           "named": sorted({c["data"]["name"] for c in com if c["data"].get("name")})[:12]})
    if not commercial["sufficient"]:
        commercial["status"] = "Insufficient data"

    # ---- competition (observed, de-duplicated)
    comp = _competitors(by["competition"], prop_point)
    competition = {"observations": len(by["competition"]), "competitors": len(comp), "sufficient": enough_cov,
                   "by_type": _dist(c["competitor_type"] for c in comp), "by_size": _dist(c["size"] for c in comp),
                   "customer_activity": _dist(c["customer_activity"] for c in comp), "list": comp[:30],
                   "nearest_to_property_m": next((c["distance_to_property_m"] for c in comp
                                                  if c["distance_to_property_m"] is not None), None)}
    if not enough_cov:
        competition["status"] = "Insufficient data: too little of the catchment was surveyed to say how many competitors exist"

    # ---- traffic / footfall (levels, never exact counts)
    tr = by["traffic"]
    traffic = {"observations": len(tr), "sufficient": rated_ok("traffic"),
               "note": "Observed levels, not counted volumes."}
    if tr:
        traffic.update({"pedestrian": _dist(c["data"]["pedestrian"] for c in tr),
                        "vehicle": _dist(c["data"]["vehicle"] for c in tr),
                        "observation_periods": _dist(c["data"]["observation_period"] for c in tr),
                        "peak_notes": [c["data"]["peak_notes"] for c in tr if c["data"].get("peak_notes")][:5]})
    if not traffic["sufficient"]:
        traffic["status"] = "Insufficient data"

    # ---- accessibility (+ local conditions, which the insights table has no separate column for)
    acc = by["accessibility"]
    lc = by["local_condition"]
    accessibility = {"observations": len(acc), "sufficient": rated_ok("accessibility")}
    if acc:
        widths = [c["data"]["approx_road_width_ft"] for c in acc if c["data"].get("approx_road_width_ft")]
        accessibility.update({
            "road_condition": _dist(c["data"]["road_condition"] for c in acc),
            "entry_exit": _dist(c["data"]["entry_exit"] for c in acc),
            "parking": _dist(c["data"]["parking"] for c in acc),
            "median_road_width_ft": round(statistics.median(widths), 1) if widths else None,
            "obstructions": sum(1 for c in acc if c["data"].get("obstruction")),
            "construction": sum(1 for c in acc if c["data"].get("construction")),
            "road_closures": sum(1 for c in acc if c["data"].get("road_closure")),
            "median_barriers": sum(1 for c in acc if c["data"].get("median_barrier")),
            "difficult_turns": sum(1 for c in acc if c["data"].get("difficult_turns"))})
    accessibility["local_conditions"] = {
        "observations": len(lc), "by_condition": _dist(c["data"]["condition"] for c in lc),
        "high_severity": [c["data"]["condition"] for c in lc if c["data"]["severity"] == "high"]}
    if not accessibility["sufficient"]:
        accessibility["status"] = "Insufficient data"

    # ---- demand generators
    dg = by["demand_generator"]
    demand = {"observations": len(dg), "sufficient": enough_cov, "by_kind": _dist(c["data"]["kind"] for c in dg),
              "named": sorted({c["data"]["name"] for c in dg if c["data"].get("name")})[:15]}
    if not enough_cov:
        demand["status"] = "Insufficient data: too little of the catchment was surveyed"

    summaries = {"residential": residential, "commercial": commercial, "competition": competition,
                 "traffic": traffic, "accessibility": accessibility, "demand_generator": demand}
    insufficient = [k for k, v in summaries.items() if not v["sufficient"]]
    for k in insufficient:
        flags.append(f"insufficient_{k}")
    if cov / 100 < K.REUSE_MIN_DATA_COVERAGE:
        flags.append("low_survey_coverage")
    sufficient_n = len(summaries) - len(insufficient)
    if sufficient_n < K.MIN_CATEGORIES_FOR_GROUND_SCORE:
        flags.append("insufficient_data")

    # ---- key findings: only computed values are quoted
    findings: list[str] = [f"Survey coverage: {cov:.0f}% of the planned observations were recorded "
                           f"({len(captures)} observations in total)."]
    if residential["sufficient"]:
        bits = []
        if residential.get("independent_houses") is not None:
            bits.append(f"{residential['independent_houses']} independent houses")
        if residential.get("apartment_complexes") is not None:
            bits.append(f"{residential['apartment_complexes']} apartment complexes")
        top = _top(residential["activity_level"])
        findings.append(f"Residential: {', '.join(bits) + '; ' if bits else ''}activity most often observed as "
                        f"{top} ({residential['observations']} observations).")
    if commercial["sufficient"]:
        findings.append(f"Commercial: {commercial['businesses_total']} businesses recorded "
                        f"({', '.join(f'{v} {k}' for k, v in commercial['businesses_by_kind'].items())}); activity most "
                        f"often {_top(commercial['activity_level'])}.")
    if enough_cov:
        near = competition["nearest_to_property_m"]
        findings.append(f"Competition: {competition['competitors']} competitor(s) recorded on the ground"
                        + (f", nearest {near} m from the property" if near is not None else "") + ".")
    if traffic["sufficient"]:
        findings.append(f"Footfall: pedestrian activity most often {_top(traffic['pedestrian'])}, vehicle activity most "
                        f"often {_top(traffic['vehicle'])} ({traffic['observations']} observations, levels not counts).")
    if accessibility["sufficient"]:
        findings.append(f"Access: road condition most often {_top(accessibility['road_condition'])}, entry/exit "
                        f"most often {_top(accessibility['entry_exit'])}, parking most often "
                        f"{_top(accessibility['parking'])}.")
    if enough_cov and demand["observations"]:
        findings.append("Demand generators recorded: " + ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in demand["by_kind"].items()) + ".")
    for k in insufficient:
        findings.append(f"{k.replace('_', ' ').capitalize()}: insufficient survey data to draw a conclusion.")

    # ---- risks (rules over the computed summaries)
    risks: list[dict] = []

    def risk(code, sev, text):
        risks.append({"code": code, "severity": sev, "text": text})

    if enough_cov and competition["competitors"] >= K.COMPETITION_CAP:
        risk("heavy_observed_competition", "high", f"{competition['competitors']} competitors were recorded on the ground.")
    elif enough_cov and competition["competitors"] >= 3:
        risk("observed_competition", "medium", f"{competition['competitors']} competitors were recorded on the ground.")
    near = competition["nearest_to_property_m"]
    if near is not None and near <= 150 and enough_cov:
        risk("competitor_very_close", "medium", f"A competitor was recorded {near} m from the property.")
    if accessibility["sufficient"]:
        a = accessibility
        for key, code, label in (("obstructions", "road_obstruction", "obstruction"),
                                 ("road_closures", "road_closure", "road closure"),
                                 ("difficult_turns", "difficult_turns", "difficult turns")):
            if a[key]:
                risk(code, "medium", f"{a[key]} accessibility observation(s) recorded a {label}.")
        if a["road_condition"].get("poor", 0) > a["observations"] / 2:
            risk("poor_road_condition", "medium", "Most accessibility observations recorded a poor road condition.")
    lc_high = accessibility["local_conditions"]["high_severity"]
    if lc_high:
        risk("severe_local_condition", "high", "High-severity local condition(s) recorded: " + ", ".join(sorted(set(lc_high))) + ".")
    if traffic["sufficient"] and traffic["pedestrian"].get("low", 0) > traffic["observations"] / 2:
        risk("low_observed_footfall", "medium", "Pedestrian activity was mostly observed as low.")
    if "low_survey_coverage" in flags:
        risk("low_survey_coverage", "medium", f"Only {cov:.0f}% of the planned observations were recorded.")
    if insufficient:
        risk("insufficient_data", "low", "Not enough survey data for: " + ", ".join(k.replace('_', ' ') for k in insufficient) + ".")
    order = {"high": 0, "medium": 1, "low": 2}
    risks.sort(key=lambda r: order[r["severity"]])

    # ---- independent ground indicator (never used for the M2 score)
    parts: dict[str, float] = {}
    if residential["sufficient"]:
        parts["residential"] = _level_score(residential["activity_level"])
    if commercial["sufficient"]:
        parts["commercial"] = _level_score(commercial["activity_level"])
    if traffic["sufficient"]:
        p, v = _level_score(traffic["pedestrian"]), _level_score(traffic["vehicle"])
        parts["traffic"] = (0.65 * p + 0.35 * v) if p is not None and v is not None else None
    if accessibility["sufficient"]:
        a = accessibility
        cond = {"good": 1.0, "fair": 0.6, "poor": 0.2}
        ease = {"easy": 1.0, "moderate": 0.55, "difficult": 0.15}
        park = {"easy": 1.0, "moderate": 0.6, "difficult": 0.25, "none": 0.0}
        def mean(dist, m):
            n = sum(dist.values())
            return sum(m[k] * v for k, v in dist.items()) / n if n else None
        vals = [mean(a["road_condition"], cond), mean(a["entry_exit"], ease), mean(a["parking"], park)]
        parts["accessibility"] = sum(vals) / len(vals)
    if enough_cov:
        parts["demand_generator"] = min(1.0, demand["observations"] / K.DEMAND_GEN_CAP)
        parts["competition"] = 1.0 - min(1.0, competition["competitors"] / K.COMPETITION_CAP)
    parts = {k: v for k, v in parts.items() if v is not None}
    score = None
    if len(parts) >= K.MIN_CATEGORIES_FOR_GROUND_SCORE:
        tw = sum(K.GROUND_WEIGHTS[k] for k in parts)
        score = round(sum(K.GROUND_WEIGHTS[k] * v for k, v in parts.items()) / tw * 100, 1)

    return {
        "coverage_percentage": cov,
        "residential_summary": residential, "commercial_summary": commercial, "competition_summary": competition,
        "traffic_summary": traffic, "accessibility_summary": accessibility, "demand_generator_summary": demand,
        "key_findings": findings, "risks": risks, "overall_ground_fit_score": score,
        "data_quality_flags": sorted(set(flags)), "categories_used": sorted(parts),
    }
