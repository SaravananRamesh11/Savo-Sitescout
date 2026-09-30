"""Tunable assumptions for the Opportunity Finder. Every value here is a starting point to calibrate with the BD team,
not a business fact; each can be overridden with an environment variable (names in the comments), and the values used are
saved with every run and shown in the UI.

The M1 factor weights are NOT here: they stay in scoring_constants.py and are reused unchanged.
"""
import os


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


# --- score --------------------------------------------------------------------------------------------------------
# opportunity = (1 - W) * M1_score + W * 100 * (1 - scouting_coverage)          (OPP_W_UNSCOUTED, 0..1)
W_UNSCOUTED = min(1.0, max(0.0, _f("OPP_W_UNSCOUTED", 0.20)))
TOP_N = int(_f("OPP_TOP_N", 10))

# --- scouting coverage ----------------------------------------------------------------------------------------------
# Units of "scouting evidence" per signal (full weight while recent), summed per cell and capped at COVERAGE_FULL_UNITS.
UNITS_PROPERTY_EVALUATED = _f("OPP_UNITS_PROPERTY_EVALUATED", 1.0)    # property captured and evaluated (M2)
UNITS_PROPERTY_UNEVALUATED = _f("OPP_UNITS_PROPERTY_UNEVALUATED", 0.5)  # captured, no completed evaluation yet
UNITS_OPEN_ASSIGNMENT = _f("OPP_UNITS_OPEN_ASSIGNMENT", 0.5)          # a BD executive is assigned to that hotspot cell
UNITS_STUDY_COMPLETED = _f("OPP_UNITS_STUDY_COMPLETED", 3.0)          # completed ground catchment study (M3)
UNITS_STUDY_ACTIVE = _f("OPP_UNITS_STUDY_ACTIVE", 1.5)                # catchment study requested / in progress
COVERAGE_FULL_UNITS = _f("OPP_COVERAGE_FULL_UNITS", 3.0)              # units that count as "fully scouted"
NEIGHBOUR_SHARE = _f("OPP_NEIGHBOUR_SHARE", 0.5)                      # share of a point signal credited to each of 8 neighbours
RECENT_FULL_DAYS = _f("OPP_RECENT_FULL_DAYS", 90)                     # signal at full weight up to this age
EXPIRE_DAYS = _f("OPP_EXPIRE_DAYS", 365)                              # then fades linearly to zero at this age

# --- data ----------------------------------------------------------------------------------------------------------
TILE_CELLS = int(_f("OPP_TILE_CELLS", 12))                            # tile = 12 x 12 cells = 6 km x 6 km
TILE_PAD_M = int(_f("OPP_TILE_PAD_M", 1000))                          # same 1 km padding M1 uses for proximity counts
CACHE_HOURS = int(_f("OPPORTUNITY_CACHE_HOURS", 168))                 # tile cache lifetime (7 days)
PAUSE_BETWEEN_TILES_S = _f("OPP_PAUSE_S", 1.5)                        # politeness towards the Overpass mirrors
MAX_CONSECUTIVE_TILE_FAILURES = int(_f("OPP_MAX_TILE_FAILURES", 3))   # then stop hitting Overpass for this run
MAX_DEMO_DISTANCE_KM = _f("OPP_MAX_DEMO_DISTANCE_KM", 3.0)            # cells farther than this from a table locality are not ranked
BBOX_PAD_M = 2500                                                     # padding around the demographic table's extent
RUN_STALE_MINUTES = 45                                                # a "running" run older than this is treated as dead
KEEP_RUNS = 3
OSRM_URL = os.getenv("OSRM_URL", "https://router.project-osrm.org")   # public demo server; used for the top results only


def city_bbox() -> tuple[float, float, float, float]:
    """(minlon, minlat, maxlon, maxlat): OPPORTUNITY_BBOX if set, else the bundled locality table's extent + padding,
    clipped to the Chennai region the product covers."""
    from app.services import demographics, geocoding

    env = os.getenv("OPPORTUNITY_BBOX", "").strip()
    if env:
        vals = [float(x) for x in env.split(",")]
        if len(vals) == 4:
            return tuple(vals)  # type: ignore[return-value]
    zs = demographics.load_mock()["zones"]
    lat_pad, lon_pad = BBOX_PAD_M / 111000, BBOX_PAD_M / (111000 * 0.974)
    b = geocoding.CHENNAI_BBOX
    return (max(min(z["lon"] for z in zs) - lon_pad, b[0]), max(min(z["lat"] for z in zs) - lat_pad, b[1]),
            min(max(z["lon"] for z in zs) + lon_pad, b[2]), min(max(z["lat"] for z in zs) + lat_pad, b[3]))


def config_snapshot() -> dict:
    """Saved with each run so a result can always be explained with the assumptions that produced it."""
    return {"w_unscouted": W_UNSCOUTED, "top_n": TOP_N, "coverage_full_units": COVERAGE_FULL_UNITS,
            "units": {"property_evaluated": UNITS_PROPERTY_EVALUATED, "property_unevaluated": UNITS_PROPERTY_UNEVALUATED,
                      "open_assignment": UNITS_OPEN_ASSIGNMENT, "study_completed": UNITS_STUDY_COMPLETED,
                      "study_active": UNITS_STUDY_ACTIVE},
            "neighbour_share": NEIGHBOUR_SHARE, "recent_full_days": RECENT_FULL_DAYS, "expire_days": EXPIRE_DAYS,
            "tile_cells": TILE_CELLS, "tile_pad_m": TILE_PAD_M, "cache_hours": CACHE_HOURS,
            "max_demo_distance_km": MAX_DEMO_DISTANCE_KM, "bbox": [round(x, 4) for x in city_bbox()]}
