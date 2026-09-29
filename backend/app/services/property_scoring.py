"""Deterministic property scoring. Pure functions, no I/O.

Input `x` (all keys optional; missing means "Insufficient data", never a guess):
  property fields (monthly_rent, expected_monthly_revenue, total_area_sqft, sales_area_sqft, storage_area_sqft,
  frontage_ft, road_width_ft, visibility_score, entry_access, exit_access, is_main_road_frontage,
  is_corner_property, traffic_signal_nearby, two/four_wheeler_parking, parking_capacity, parking_type)
  poi: {"available": bool, "counts": {"school": {250: n, 500: n}, ...}, "organised": {500: n, 1000: n},
        "other_grocery": {...}, "nearest_organised_m": float|None}  (None/absent when no real OSM data)
  field_organised_competitors: int
  demo: {"household_density":..., "pop_density":..., "growth_pct":..., "mocked": bool}
  m1: output of m1_lookup.m1_context
Weights are renormalised over the factors that could be scored; `confidence` is the share of weight scored.
"""
from app.core import property_scoring_constants as K


def _clip(v: float) -> float:
    return max(0.0, min(1.0, v))


def _wavg(parts: list[tuple[float, float | None]]) -> float | None:
    """Weighted mean over the parts that have a value; None if there are none."""
    have = [(w, v) for w, v in parts if v is not None]
    if not have:
        return None
    tw = sum(w for w, _ in have)
    return sum(w * v for w, v in have) / tw


def derived_metrics(x: dict) -> dict:
    rent, total = x.get("monthly_rent"), x.get("total_area_sqft")
    rev, sales, storage = x.get("expected_monthly_revenue"), x.get("sales_area_sqft"), x.get("storage_area_sqft")
    m: dict = {"rent_per_sqft": None, "rent_to_revenue": None, "sales_ratio": None, "storage_ratio": None,
               "storage_to_sales": None}
    if rent and total:
        m["rent_per_sqft"] = round(float(rent) / float(total), 2)
    if rent and rev:
        m["rent_to_revenue"] = round(float(rent) / float(rev), 4)
    if sales and total:
        m["sales_ratio"] = round(float(sales) / float(total), 3)
    if storage and total:
        m["storage_ratio"] = round(float(storage) / float(total), 3)
    if storage and sales:
        m["storage_to_sales"] = round(float(storage) / float(sales), 3)
    return m


# ---------------------------------------------------------------- individual factors: (norm|None, raw, text)
def f_rent(x, m):
    r = m["rent_to_revenue"]
    if r is None:
        why = ("Insufficient data: no expected monthly revenue was provided, so rent affordability cannot be "
               "scored. Rent per sq ft is shown for reference.")
        return None, None, why
    lo, hi, zero = K.RENT_TO_REVENUE_TARGET_MIN, K.RENT_TO_REVENUE_TARGET_MAX, K.RENT_TO_REVENUE_ZERO_AT
    norm = 1.0 if r <= hi else _clip(1 - (r - hi) / (zero - hi))
    band = "within" if lo <= r <= hi else ("below" if r < lo else "above")
    return norm, r, (f"Rent is {r * 100:.1f}% of expected revenue ({x.get('revenue_source') or 'supplied estimate'}), "
                     f"{band} the configurable {lo * 100:.0f}-{hi * 100:.0f}% target band (an assumption, not a "
                     f"validated Savomart rule)")


def f_access(x, _m):
    lvl = K.ACCESS_LEVEL
    parts = []
    if x.get("is_main_road_frontage") is not None:
        parts.append((0.25, 1.0 if x["is_main_road_frontage"] else 0.3))
    if x.get("visibility_score") is not None:
        parts.append((0.20, (x["visibility_score"] - 1) / 4))
    if x.get("frontage_ft") is not None:
        parts.append((0.15, _clip(x["frontage_ft"] / K.FRONTAGE_FULL_FT)))
    if x.get("road_width_ft") is not None:
        parts.append((0.15, _clip(x["road_width_ft"] / K.ROAD_WIDTH_FULL_FT)))
    if x.get("entry_access"):
        parts.append((0.10, lvl.get(x["entry_access"])))
    if x.get("exit_access"):
        parts.append((0.10, lvl.get(x["exit_access"])))
    if x.get("is_corner_property") is not None:
        parts.append((0.05, 1.0 if x["is_corner_property"] else 0.5))
    base = _wavg(parts)
    if base is None:
        return None, None, "Insufficient data: no frontage, road or visibility details were captured."
    note = ""
    mod = 0.0
    if x.get("traffic_signal_nearby"):
        ent, ext = lvl.get(x.get("entry_access") or ""), lvl.get(x.get("exit_access") or "")
        ee = [v for v in (ent, ext) if v is not None]
        if ee and min(ee) <= lvl["difficult"]:
            mod, note = -K.SIGNAL_PENALTY, "; a nearby traffic signal with difficult entry/exit counts against it"
        elif ee and min(ee) >= lvl["moderate"] and sum(ee) / len(ee) >= 0.775:
            mod, note = K.SIGNAL_BONUS, "; a nearby signal adds a little because entry/exit are easy"
        else:
            note = "; a nearby traffic signal is treated as neutral (traffic, but not proven access)"
    norm = _clip(base + mod)
    used = ", ".join(n for n, k in (("main road", "is_main_road_frontage"), ("visibility", "visibility_score"),
                                    ("frontage", "frontage_ft"), ("road width", "road_width_ft"),
                                    ("entry/exit", "entry_access"), ("corner", "is_corner_property"))
                     if x.get(k) is not None)
    return norm, round(base, 3), f"Based on {used}{note}"


def f_demand(x, _m):
    poi = x.get("poi") or {}
    if not poi.get("available"):
        return None, None, "Insufficient data: no real OpenStreetMap data was available around this property."
    c = poi["counts"]
    parts = []
    for cat, cap in K.DEMAND_CAPS_500M.items():
        parts.append((K.DEMAND_INTERNAL_WEIGHTS[cat], _clip(c.get(cat, {}).get(500, 0) / cap)))
    norm = _wavg(parts)
    bits = [f"{c.get(cat, {}).get(250, 0)}/{c.get(cat, {}).get(500, 0)} {cat}s (250 m / 500 m)"
            for cat in K.DEMAND_CAPS_500M]
    return norm, round(norm, 3), ("Trip-generator signals, not guaranteed customers: " + ", ".join(bits))


def f_demographic(x, _m):
    d = x.get("demo") or {}
    hd = d.get("household_density")
    if hd is None:
        return None, None, "Insufficient data: no demographic estimate for this location."
    mn, full = K.TARGET_HOUSEHOLD_DENSITY_MIN, K.TARGET_HOUSEHOLD_DENSITY_FULL
    norm = 0.5 * _clip(hd / mn) if hd < mn else 0.5 + 0.5 * _clip((hd - mn) / (full - mn))
    tag = " (estimated, not official Census data)" if d.get("mocked") else ""
    return norm, hd, (f"{hd:,.0f} households/km2 against a target of {mn:,.0f} (half credit) to {full:,.0f} (full)"
                      f"{tag}. Income fit is not assessed: there is no income data or Savomart target range.")


def f_competition(x, _m, demand_norm):
    poi = x.get("poi") or {}
    if not poi.get("available"):
        return None, None, "Insufficient data: no real OpenStreetMap competitor data around this property."
    osm_org = poi["organised"].get(1000, 0)
    field = int(x.get("field_organised_competitors") or 0)
    org = max(osm_org, field)  # the two sources can describe the same store, so never add them
    d = demand_norm if demand_norm is not None else 0.5
    pressure = _clip(org / K.ORGANISED_CAP)
    if org == 0:
        norm = 0.5 + 0.5 * d  # no competition is only good when demand exists
        txt = ("No organised grocery competitor within 1 km. That is an opportunity only if demand is real, so it "
               "is scored together with demand" + (" (demand signals are weak)" if d < 0.4 else ""))
    else:
        norm = 1 - pressure * (1 - 0.5 * d)
        txt = (f"{org} organised competitor(s) within 1 km ({osm_org} from OpenStreetMap, {field} seen in the field; "
               f"not added together). Strong demand tolerates more competition")
    return _clip(norm), org, txt


def f_parking(x, _m):
    pt = {"on_property": 1.0, "both": 1.0, "roadside": 0.5, "none": 0.0}
    parts = []
    if x.get("two_wheeler_parking") is not None:
        parts.append((0.25, 1.0 if x["two_wheeler_parking"] else 0.0))
    if x.get("four_wheeler_parking") is not None:
        parts.append((0.30, 1.0 if x["four_wheeler_parking"] else 0.0))
    if x.get("parking_capacity") is not None:
        parts.append((0.25, _clip(x["parking_capacity"] / K.PARKING_CAPACITY_FULL)))
    if x.get("parking_type"):
        parts.append((0.20, pt.get(x["parking_type"])))
    norm = _wavg(parts)
    if norm is None:
        return None, None, "Insufficient data: parking was not captured."
    return norm, round(norm, 3), (f"Two-wheeler: {_yn(x.get('two_wheeler_parking'))}, four-wheeler: "
                                  f"{_yn(x.get('four_wheeler_parking'))}, capacity: "
                                  f"{x.get('parking_capacity') if x.get('parking_capacity') is not None else 'not given'}"
                                  f", type: {x.get('parking_type') or 'not given'}")


def _yn(v):
    return "not captured" if v is None else ("yes" if v else "no")


def f_space(x, m):
    total = x.get("total_area_sqft")
    if not total:
        return None, None, "Insufficient data: total area is missing."
    if m["sales_ratio"] is not None:
        lo, hi = K.SALES_RATIO_TARGET
        r = m["sales_ratio"]
        norm = 1.0 if lo <= r <= hi else _clip(1 - (lo - r if r < lo else r - hi) / 0.3)
        st = f", storage-to-sales {m['storage_to_sales']:.2f}" if m["storage_to_sales"] is not None else ""
        return norm, r, (f"Sales area is {r * 100:.0f}% of usable area (configurable target "
                         f"{lo * 100:.0f}-{hi * 100:.0f}%; no universal 80:20 rule assumed){st}")
    lo, hi = K.ADEQUATE_AREA_SQFT
    norm = 1.0 if lo <= total <= hi else (_clip(total / lo) if total < lo else _clip(hi / total))
    return norm, total, (f"Only total area is known ({total:,.0f} sq ft); scored against a configurable adequacy band "
                         f"of {lo:,.0f}-{hi:,.0f} sq ft. Sales/storage split was not measured")


def f_area(x, _m):
    m1 = x.get("m1") or {}
    cell, area = m1.get("cell_score"), m1.get("area_score")
    vals = [v for v in (cell, area) if v is not None]
    if not vals:
        return None, None, "Insufficient data: this area has no completed M1 area analysis yet."
    v = sum(vals) / len(vals)
    bits = []
    if cell is not None:
        bits.append(f"grid-cell score {cell:.1f}" + (" (hotspot)" if m1.get("is_hotspot") else ""))
    if area is not None:
        bits.append(f"area score {area:.1f}")
    return _clip(v / 100), round(v, 1), "M1: " + " and ".join(bits)


FACTORS = ["rent_affordability", "accessibility", "demand_generators", "demographic_fit", "competition",
           "parking_access", "space_utilisation", "area_fitness"]


def score_property(x: dict) -> dict:
    m = derived_metrics(x)
    demand = f_demand(x, m)
    results = {
        "rent_affordability": f_rent(x, m), "accessibility": f_access(x, m), "demand_generators": demand,
        "demographic_fit": f_demographic(x, m), "competition": f_competition(x, m, demand[0]),
        "parking_access": f_parking(x, m), "space_utilisation": f_space(x, m), "area_fitness": f_area(x, m),
    }
    total_w = sum(K.WEIGHTS[k] for k in FACTORS)
    scored_w = sum(K.WEIGHTS[k] for k in FACTORS if results[k][0] is not None)
    mocked = set()
    if (x.get("demo") or {}).get("mocked"):
        mocked.add("demographic_fit")
    if "area_fitness" in results and results["area_fitness"][0] is not None and any(
            "mock" in f for f in (x.get("m1") or {}).get("report_flags", [])):
        mocked.add("area_fitness")
    breakdown = []
    for k in FACTORS:
        norm, raw, why = results[k]
        w = K.WEIGHTS[k]
        eff = (w / scored_w * 100) if (scored_w and norm is not None) else 0.0
        breakdown.append({
            "key": k, "label": K.FACTOR_LABELS[k], "weight": w, "effective_weight": round(eff, 2),
            "scored": norm is not None, "norm": None if norm is None else round(norm, 4),
            "points": None if norm is None else round(norm * eff, 2),
            "raw": raw if not isinstance(raw, float) else round(raw, 4), "explanation": why,
            "estimated_input": k in mocked})
    if scored_w == 0:
        return {"total": None, "confidence": 0.0, "breakdown": breakdown, "metrics": m, "unscored": FACTORS}
    total = round(sum(b["points"] for b in breakdown if b["points"] is not None), 1)
    mocked_w = sum(K.WEIGHTS[k] for k in mocked if results[k][0] is not None)
    confidence = round(max(0.0, (scored_w - 0.5 * mocked_w) / total_w), 3)
    return {"total": total, "confidence": confidence, "breakdown": breakdown, "metrics": m,
            "unscored": [k for k in FACTORS if results[k][0] is None]}


# ---------------------------------------------------------------- risks and recommendation (deterministic rules)
def build_risks(x: dict, result: dict, flags: list[str], extra: dict) -> list[dict]:
    risks: list[dict] = []

    def add(code, severity, text):
        risks.append({"code": code, "severity": severity, "text": text})

    m1 = x.get("m1") or {}
    d = m1.get("nearest_savomart_m")
    if d is not None and d < K.CANNIBALISATION_M:
        add("cannibalisation", "high", f"The nearest Savomart ({m1.get('nearest_savomart')}) is only {d:,.0f} m away, "
                                       f"inside the {K.CANNIBALISATION_M:,.0f} m cannibalisation distance.")
    poi = x.get("poi") or {}
    if poi.get("available"):
        org = max(poi["organised"].get(1000, 0), int(x.get("field_organised_competitors") or 0))
        if org >= K.ORGANISED_CAP:
            add("organised_competition", "high", f"{org} organised grocery competitors within 1 km.")
        elif org >= 2:
            add("organised_competition", "medium", f"{org} organised grocery competitors within 1 km.")
    else:
        add("no_osm_data", "medium", "No real OpenStreetMap data was available, so demand and competition are unscored.")
    if x.get("entry_access") == "difficult" or x.get("exit_access") == "difficult":
        add("difficult_access", "high" if x.get("traffic_signal_nearby") else "medium",
            "Entry or exit is difficult" + (" and a traffic signal is nearby, which can make queuing worse." if
                                            x.get("traffic_signal_nearby") else "."))
    fr = x.get("frontage_ft")
    if fr is not None and fr < K.FRONTAGE_MIN_FT:
        add("narrow_frontage", "medium", f"Frontage is only {fr:g} ft (below {K.FRONTAGE_MIN_FT:g} ft).")
    acc = x.get("location_accuracy_m")
    if acc is not None and acc > K.POOR_GPS_ACCURACY_M and x.get("location_source") != "manual_pin":
        add("poor_gps", "low", f"GPS accuracy was about {acc:,.0f} m; the pin may be off the building.")
    if extra.get("pin_far_from_assignment_m"):
        add("pin_far_from_assignment", "medium",
            f"The pin is {extra['pin_far_from_assignment_m']:,.0f} m from the assigned hotspot/area.")
    if extra.get("duplicate_flags"):
        add("possible_duplicate", "medium", "Possible duplicate of another property; review before proceeding.")
    if "rent_affordability" in result["unscored"]:
        add("rent_unscored", "low", "Rent affordability could not be scored (no expected revenue provided).")
    if m1.get("area_report_id") is None:
        add("no_m1_report", "low", "This area has no completed M1 analysis; run one for full context.")
    for f, text in (("population_mocked", "Population and household figures are estimates, not official data."),
                    ("osm_mocked", "The area analysis used synthetic OpenStreetMap placeholders."),
                    ("osm_stale_cache", "OpenStreetMap data was served from an older cache.")):
        if f in flags or f in (m1.get("report_flags") or []):
            add(f, "low", text)
    if result["confidence"] < K.LOW_CONFIDENCE:
        add("low_confidence", "medium", f"Only {result['confidence'] * 100:.0f}% of the scoring weight could be "
                                        f"evaluated; treat the score as provisional.")
    order = {"high": 0, "medium": 1, "low": 2}
    return sorted(risks, key=lambda r: order[r["severity"]])


def recommend(total: float | None, confidence: float, risks: list[dict]) -> tuple[str, str]:
    if total is None:
        return "REVIEW", "Not enough data to score this property; review manually."
    hard = [r for r in risks if r["severity"] == "high"]
    if total >= K.PROCEED_MIN_SCORE and not hard and confidence >= K.LOW_CONFIDENCE:
        return "PROCEED_TO_CATCHMENT", "Scores well with no high-severity risks: worth a catchment study."
    if total < K.REVIEW_MIN_SCORE:
        return "NOT_RECOMMENDED", f"Score {total:.1f} is below the review threshold ({K.REVIEW_MIN_SCORE:.0f})."
    if hard:
        return "REVIEW", "Decent score but has high-severity risk(s): " + "; ".join(r["code"] for r in hard) + "."
    return "REVIEW", "Mid-range score or limited data: manager judgement needed."
