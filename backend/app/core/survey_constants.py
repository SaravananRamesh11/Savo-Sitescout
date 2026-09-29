"""M3 (ground catchment survey) constants.

Every value is a documented, configurable ASSUMPTION for the first version, not a validated Savomart rule.
Override through environment variables where useful.
"""
import os


def _f(name: str, default: float) -> float:
    return float(os.getenv(name, default))


def _i(name: str, default: int) -> int:
    return int(os.getenv(name, default))


# ---- study geometry
CATCHMENT_RADIUS_M = _f("CATCHMENT_RADIUS_M", 500)  # property studies: circle around the pin
STUDY_MAX_KM2 = _f("STUDY_MAX_KM2", 6)  # area studies larger than this are refused (too much ground for one study)

# ---- reuse of an existing completed study (deterministic; simple on purpose)
REUSE_MAX_AGE_DAYS = _i("REUSE_MAX_AGE_DAYS", 90)  # fresh enough
REUSE_MIN_COVERAGE = _f("REUSE_MIN_COVERAGE", 0.80)  # share of the NEW study area inside the old study geometry
REUSE_MIN_DATA_COVERAGE = _f("REUSE_MIN_DATA_COVERAGE", 0.70)  # insights.coverage_percentage of the old study (0-1)

# ---- work-unit split (heuristic, no optimiser)
BLOCK_M = _i("SPLIT_BLOCK_M", 125)  # the study is partitioned into blocks of this size, then blocks are grouped
TARGET_POINTS_PER_UNIT = _f("TARGET_POINTS_PER_UNIT", 45)  # workload points one unit should hold (about an hour on foot)
MAX_UNITS = _i("MAX_UNITS", 12)
W_ROAD_PER_100M = 1.0  # 1 point per 100 m of lane
W_COMMERCIAL_POI = 0.5  # per shop / office / bank / market
W_AMENITY_POI = 0.5  # per school / college / hospital / clinic / transit stop
HOUSEHOLDS_PER_POINT = _f("HOUSEHOLDS_PER_POINT", 50)  # estimated households (M1 estimate, flagged) per point
CAPTURES_PER_POINT = _f("CAPTURES_PER_POINT", 0.35)  # expected observations per workload point
MIN_TARGET_CAPTURES, MAX_TARGET_CAPTURES = 6, 40

# ---- captures
UNIT_TOLERANCE_M = _f("UNIT_TOLERANCE_M", 40)  # a capture must be inside the unit polygon (with this GPS tolerance)
POOR_GPS_ACCURACY_M = _f("SURVEY_POOR_GPS_M", 100)
MAX_PHOTOS_PER_CAPTURE = 6

# ---- insights
MIN_CAPTURES_PER_CATEGORY = _i("MIN_CAPTURES_PER_CATEGORY", 2)  # rated categories need at least this many observations
COMPETITOR_DEDUPE_M = _f("COMPETITOR_DEDUPE_M", 25)  # same name + type within this distance = one competitor
MIN_CATEGORIES_FOR_GROUND_SCORE = 3
COMPETITION_CAP = _i("GROUND_COMPETITION_CAP", 6)  # observed competitors at which competition pressure is maximal
DEMAND_GEN_CAP = _i("GROUND_DEMAND_GEN_CAP", 5)  # demand generators at which the signal is maximal
# weights of the ground-survey indicator (independent of the M2 score, never feeds it)
GROUND_WEIGHTS = {"residential": 20, "commercial": 15, "traffic": 20, "accessibility": 20,
                  "demand_generator": 15, "competition": 10}
LEVEL_VALUE = {"low": 0.2, "medium": 0.6, "high": 1.0}
