"""Named scoring constants: initial product assumptions, documented in the README.

Weights follow the M1 spec and sum to 100. Caps are the value at which a factor earns
full points; they are calibratable product assumptions, not measured truths.
"""

WEIGHTS = {
    # Residential demand 35
    "population_density": 15,
    "household_density": 15,
    "population_growth": 5,
    # Accessibility 20
    "road_density": 8,
    "major_road_proximity": 7,
    "transit_access": 5,
    # Commercial activity 10
    "commercial_activity": 10,
    # Amenity activity 10
    "amenity_activity": 10,
    # Competition opportunity 15 (inverse)
    "competition_opportunity": 15,
    # Savomart opportunity 10 (distance to nearest operational Savomart store)
    "savomart_opportunity": 10,
}
GROUPS = {
    "Residential demand": ["population_density", "household_density", "population_growth"],
    "Accessibility": ["road_density", "major_road_proximity", "transit_access"],
    "Commercial activity": ["commercial_activity"],
    "Amenity activity": ["amenity_activity"],
    "Competition opportunity": ["competition_opportunity"],
    "Savomart opportunity": ["savomart_opportunity"],
}

POP_DENSITY_CAP = 30_000  # people / km^2 -> full points
HOUSEHOLD_DENSITY_CAP = 7_500  # households / km^2 -> full points
GROWTH_CAP_PCT = 4.0  # % per year -> full points (<=0 earns 0)
ROAD_DENSITY_CAP_KM = 20.0  # km of road / km^2
MAJOR_ROAD_FULL_M = 200  # within this distance of a trunk/primary/secondary road -> full points
MAJOR_ROAD_ZERO_M = 2_000  # at/after this distance -> 0
TRANSIT_RADIUS_M = 500
TRANSIT_CAP = 4  # transit stops within radius -> full points
COMMERCIAL_CAP_PER_KM2 = 500  # shops + offices + banks + markets per km^2
AMENITY_CAP_PER_KM2 = 25  # weighted amenity units per km^2
AMENITY_WEIGHTS = {"school": 1.0, "college": 1.0, "hospital": 2.0, "clinic": 0.5}
COMPETITION_RADIUS_M = 1_000
COMPETITION_CAP = 15  # >= this many supermarkets/convenience stores within radius -> 0 points
SAVOMART_ZERO_M = 500  # nearer than this -> cannibalisation, 0 points
SAVOMART_FULL_M = 3_000  # farther than this -> full points

RATING_BANDS = [(75, "Excellent fit"), (60, "Good fit"), (45, "Moderate fit"), (0, "Weak fit")]
