"""M2 property evaluation constants.

EVERY value here is a documented, configurable ASSUMPTION for the first version. None is a validated Savomart
business rule. Override the ones that matter through environment variables (see _f/_i helpers) or edit this file.
"""
import os


def _f(name: str, default: float) -> float:
    return float(os.getenv(name, default))


def _i(name: str, default: int) -> int:
    return int(os.getenv(name, default))


# Factor weights (sum to 100). Renormalised over the factors that can actually be scored for a property.
WEIGHTS = {
    "rent_affordability": _f("W_RENT", 15),
    "accessibility": _f("W_ACCESS", 15),
    "demand_generators": _f("W_DEMAND", 15),
    "demographic_fit": _f("W_DEMOGRAPHIC", 10),
    "competition": _f("W_COMPETITION", 15),
    "parking_access": _f("W_PARKING", 10),
    "space_utilisation": _f("W_SPACE", 10),
    "area_fitness": _f("W_AREA", 10),
}
FACTOR_LABELS = {
    "rent_affordability": "Rent affordability",
    "accessibility": "Accessibility and visibility",
    "demand_generators": "Nearby demand generators",
    "demographic_fit": "Demographic fit",
    "competition": "Competition vs demand",
    "parking_access": "Parking",
    "space_utilisation": "Space utilisation",
    "area_fitness": "M1 area fitness",
}

# --- rent (INITIAL ASSUMPTION: 3-5% rent-to-revenue; not a validated Savomart rule) ---
RENT_TO_REVENUE_TARGET_MIN = _f("RENT_TO_REVENUE_TARGET_MIN", 0.03)
RENT_TO_REVENUE_TARGET_MAX = _f("RENT_TO_REVENUE_TARGET_MAX", 0.05)
RENT_TO_REVENUE_ZERO_AT = _f("RENT_TO_REVENUE_ZERO_AT", 0.10)  # ratio at/above which the factor scores 0
RENT_PER_SQFT_SANITY = (_f("RENT_PSF_MIN", 10), _f("RENT_PSF_MAX", 400))  # Rs/sqft/month plausibility band (warning)
MONTHLY_RENT_SANITY = (_f("RENT_MIN", 3000), _f("RENT_MAX", 1_500_000))

# --- access ---
ROAD_WIDTH_FULL_FT = _f("ROAD_WIDTH_FULL_FT", 40)
FRONTAGE_FULL_FT = _f("FRONTAGE_FULL_FT", 20)
FRONTAGE_MIN_FT = _f("FRONTAGE_MIN_FT", 10)  # below this is a risk
SIGNAL_NEAR_M = _f("SIGNAL_NEAR_M", 100)
SIGNAL_BONUS, SIGNAL_PENALTY = 0.05, 0.10  # modifier size on the 0..1 accessibility score
ACCESS_LEVEL = {"easy": 1.0, "moderate": 0.55, "difficult": 0.15}

# --- demand generators (trip generators, not guaranteed customers) ---
DEMAND_RADII_M = (250, 500)
DEMAND_CAPS_500M = {"school": 3, "college": 1, "hospital": 1}  # counts within 500 m that earn full credit
DEMAND_INTERNAL_WEIGHTS = {"school": 0.4, "college": 0.25, "hospital": 0.35}

# --- demographics (target profile is a configurable assumption; income is NOT assessed: no data, no target) ---
TARGET_HOUSEHOLD_DENSITY_MIN = _f("TARGET_HOUSEHOLD_DENSITY_MIN", 2500)  # households/km2 -> half credit
TARGET_HOUSEHOLD_DENSITY_FULL = _f("TARGET_HOUSEHOLD_DENSITY_FULL", 7500)  # -> full credit (same cap as M1)

# --- competition ---
COMPETITION_RADII_M = (500, 1000)
ORGANISED_BRANDS = [b.strip().lower() for b in os.getenv(
    "ORGANISED_BRANDS",
    "reliance,smart,more,nilgiri,spencer,dmart,d mart,big bazaar,star bazaar,foodworld,lulu,heritage,vijay,"
    "godrej,easyday,ratnadeep,nature's basket,natures basket,hypermarket",
).split(",") if b.strip()]
ORGANISED_CAP = _i("ORGANISED_CAP", 4)  # organised competitors within 1 km at which pressure is maximal
CANNIBALISATION_M = _f("SAVOMART_CANNIBALISATION_M", 500)

# --- space (no 80:20 rule assumed) ---
ADEQUATE_AREA_SQFT = (_f("AREA_MIN_SQFT", 800), _f("AREA_MAX_SQFT", 3000))  # total-area band when sales/storage unknown
SALES_RATIO_TARGET = (_f("SALES_RATIO_MIN", 0.6), _f("SALES_RATIO_MAX", 0.85))

# --- parking ---
PARKING_CAPACITY_FULL = _i("PARKING_CAPACITY_FULL", 6)

# --- validation / duplicates ---
REQUIRED_PHOTO_TYPES = [t.strip() for t in os.getenv("REQUIRED_PHOTO_TYPES", "front_view,road_view,interior_view").split(",") if t.strip()]
DUPLICATE_RADIUS_M = _f("DUPLICATE_RADIUS_M", 20)
MAX_PIN_FROM_ASSIGNMENT_M = _f("MAX_PIN_FROM_ASSIGNMENT_M", 1000)
POOR_GPS_ACCURACY_M = _f("POOR_GPS_ACCURACY_M", 100)

# --- recommendation bands (on the renormalised 0-100 score) ---
PROCEED_MIN_SCORE = _f("PROCEED_MIN_SCORE", 65)
REVIEW_MIN_SCORE = _f("REVIEW_MIN_SCORE", 45)
LOW_CONFIDENCE = _f("LOW_CONFIDENCE", 0.5)
