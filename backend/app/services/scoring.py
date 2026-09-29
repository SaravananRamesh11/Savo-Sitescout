"""Deterministic fitness scoring. Pure functions, no I/O: unit-testable and fully explainable.

Input `features` (per grid cell or aggregated for a whole area):
  pop_density (people/km2), household_density (hh/km2), growth_pct (%/yr),
  road_km_per_km2, major_road_dist_m, transit_count, commercial_per_km2,
  amenity_units_per_km2, competitor_count, savomart_dist_m (None = no store known)
"""
from app.core import scoring_constants as K


def _clip(x: float) -> float:
    return max(0.0, min(1.0, x))


def _linear(x: float, zero_at: float, full_at: float) -> float:
    if full_at == zero_at:
        return 1.0
    return _clip((x - zero_at) / (full_at - zero_at))


def rating_for(score: float) -> str:
    for threshold, label in K.RATING_BANDS:
        if score >= threshold:
            return label
    return K.RATING_BANDS[-1][1]


def _factor(key, label, group, raw, unit, norm, explanation):
    w = K.WEIGHTS[key]
    return {
        "key": key,
        "label": label,
        "group": group,
        "weight": w,
        "raw": None if raw is None else round(float(raw), 2),
        "unit": unit,
        "norm": round(norm, 4),
        "points": round(norm * w, 2),
        "explanation": explanation,
    }


def score_features(f: dict) -> dict:
    fac = []
    pd = f.get("pop_density", 0.0) or 0.0
    fac.append(_factor("population_density", "Population density", "Residential demand", pd, "people/km²",
                       _clip(pd / K.POP_DENSITY_CAP),
                       f"{pd:,.0f} people/km² vs {K.POP_DENSITY_CAP:,} for full points"))
    hd = f.get("household_density", 0.0) or 0.0
    fac.append(_factor("household_density", "Household density", "Residential demand", hd, "households/km²",
                       _clip(hd / K.HOUSEHOLD_DENSITY_CAP),
                       f"{hd:,.0f} households/km² vs {K.HOUSEHOLD_DENSITY_CAP:,} for full points"))
    g = f.get("growth_pct", 0.0) or 0.0
    fac.append(_factor("population_growth", "Population growth", "Residential demand", g, "%/yr",
                       _clip(g / K.GROWTH_CAP_PCT),
                       f"{g:.1f}%/yr growth vs {K.GROWTH_CAP_PCT:.0f}%/yr for full points"))
    rd = f.get("road_km_per_km2", 0.0) or 0.0
    fac.append(_factor("road_density", "Road density", "Accessibility", rd, "km/km²",
                       _clip(rd / K.ROAD_DENSITY_CAP_KM),
                       f"{rd:.1f} km of road per km² vs {K.ROAD_DENSITY_CAP_KM:.0f} for full points"))
    mr = f.get("major_road_dist_m")
    mr_norm = 0.0 if mr is None else 1.0 - _linear(mr, K.MAJOR_ROAD_FULL_M, K.MAJOR_ROAD_ZERO_M)
    fac.append(_factor("major_road_proximity", "Major-road proximity", "Accessibility", mr, "m",
                       mr_norm,
                       "no major road found nearby" if mr is None else
                       f"{mr:,.0f} m to nearest trunk/primary/secondary road"))
    tc = f.get("transit_count", 0.0) or 0.0
    fac.append(_factor("transit_access", "Transit access", "Accessibility", tc, "stops",
                       _clip(tc / K.TRANSIT_CAP),
                       f"{tc:.1f} transit stops within {K.TRANSIT_RADIUS_M} m vs {K.TRANSIT_CAP} for full points"))
    cm = f.get("commercial_per_km2", 0.0) or 0.0
    fac.append(_factor("commercial_activity", "Commercial activity", "Commercial activity", cm, "POIs/km²",
                       _clip(cm / K.COMMERCIAL_CAP_PER_KM2),
                       f"{cm:,.0f} shops/offices/banks per km² vs {K.COMMERCIAL_CAP_PER_KM2} for full points"))
    am = f.get("amenity_units_per_km2", 0.0) or 0.0
    fac.append(_factor("amenity_activity", "Amenity activity", "Amenity activity", am, "units/km²",
                       _clip(am / K.AMENITY_CAP_PER_KM2),
                       f"{am:.1f} weighted amenity units per km² vs {K.AMENITY_CAP_PER_KM2} for full points"))
    cc = f.get("competitor_count", 0.0) or 0.0
    fac.append(_factor("competition_opportunity", "Competition opportunity", "Competition opportunity", cc,
                       "stores",
                       1.0 - _clip(cc / K.COMPETITION_CAP),
                       f"{cc:.1f} supermarkets/convenience stores within {K.COMPETITION_RADIUS_M / 1000:.0f} km "
                       f"(fewer is better; {K.COMPETITION_CAP}+ scores 0)"))
    sd = f.get("savomart_dist_m")
    sv_norm = 1.0 if sd is None else _linear(sd, K.SAVOMART_ZERO_M, K.SAVOMART_FULL_M)
    fac.append(_factor("savomart_opportunity", "Savomart opportunity", "Savomart opportunity", sd, "m",
                       sv_norm,
                       "no operational Savomart store known" if sd is None else
                       f"{sd:,.0f} m to nearest Savomart (<{K.SAVOMART_ZERO_M} m risks cannibalisation, "
                       f"{K.SAVOMART_FULL_M / 1000:.0f} km+ is full opportunity)"))
    total = round(sum(x["points"] for x in fac), 1)
    return {"total": total, "rating": rating_for(total), "breakdown": fac}
