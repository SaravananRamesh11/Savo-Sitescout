from app.services.scoring import rating_for, score_features

# Hand-computed example (every factor calculated by hand from app/core/scoring_constants.py).
EXAMPLE = {
    "pop_density": 22_500,          # 0.75 * 15 = 11.25
    "household_density": 6_000,     # 0.80 * 15 = 12.00
    "growth_pct": 2.0,              # 0.50 * 5  =  2.50
    "road_km_per_km2": 15.0,        # 0.75 * 8  =  6.00
    "major_road_dist_m": 200,       # 1.00 * 7  =  7.00
    "transit_count": 2,             # 0.50 * 5  =  2.50
    "commercial_per_km2": 400,      # 0.80 * 10 =  8.00
    "amenity_units_per_km2": 15,    # 0.60 * 10 =  6.00
    "competitor_count": 3.75,       # 0.75 * 15 = 11.25
    "savomart_dist_m": 1_750,       # 0.50 * 10 =  5.00
}  # total = 71.5


def test_golden_example():
    out = score_features(EXAMPLE)
    assert out["total"] == 71.5
    assert out["rating"] == "Good fit"
    pts = {f["key"]: f["points"] for f in out["breakdown"]}
    assert pts["population_density"] == 11.25
    assert pts["competition_opportunity"] == 11.25
    assert sum(f["weight"] for f in out["breakdown"]) == 100


def test_bounds_and_monotonic():
    zero = score_features({"savomart_dist_m": 0, "competitor_count": 99, "major_road_dist_m": 99999})
    assert zero["total"] == 0.0
    best = dict(EXAMPLE, pop_density=99999, household_density=99999, growth_pct=99, road_km_per_km2=99,
                major_road_dist_m=0, transit_count=99, commercial_per_km2=9999, amenity_units_per_km2=999,
                competitor_count=0, savomart_dist_m=99999)
    assert score_features(best)["total"] == 100.0
    assert score_features(dict(EXAMPLE, competitor_count=5))["total"] < score_features(EXAMPLE)["total"]


def test_missing_store_is_full_opportunity_and_missing_road_is_zero():
    out = score_features(dict(EXAMPLE, savomart_dist_m=None, major_road_dist_m=None))
    pts = {f["key"]: f["points"] for f in out["breakdown"]}
    assert pts["savomart_opportunity"] == 10.0
    assert pts["major_road_proximity"] == 0.0


def test_rating_bands():
    assert rating_for(80) == "Excellent fit"
    assert rating_for(60) == "Good fit"
    assert rating_for(45) == "Moderate fit"
    assert rating_for(10) == "Weak fit"
