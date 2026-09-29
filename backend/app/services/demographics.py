"""Demographic estimates per location.

Ward-level Census data has no clean public API, so this uses a clearly-labelled ESTIMATED
locality table (mock_data/demographics_chennai.json) blended with inverse-distance weighting
over the 3 nearest locality centroids. Every report using it carries the flag
`population_mocked` and the UI/README label it as an estimate.
"""
import json
import math
from functools import lru_cache
from pathlib import Path

MOCK_PATH = Path(__file__).resolve().parents[1] / "mock_data" / "demographics_chennai.json"


@lru_cache(maxsize=1)
def load_mock() -> dict:
    return json.loads(MOCK_PATH.read_text(encoding="utf-8"))


def _hav_km(lat1, lon1, lat2, lon2) -> float:
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 12742 * math.asin(math.sqrt(a))


def estimate_at(lat: float, lon: float) -> dict:
    data = load_mock()
    hh = data["avg_household_size"]
    ranked = sorted(data["zones"], key=lambda z: _hav_km(lat, lon, z["lat"], z["lon"]))[:3]
    ws, pd, gr = 0.0, 0.0, 0.0
    for z in ranked:
        d = max(_hav_km(lat, lon, z["lat"], z["lon"]), 0.3)
        w = 1.0 / (d * d)
        ws += w
        pd += w * z["pop_density"]
        gr += w * z["growth_pct"]
    pd, gr = pd / ws, gr / ws
    return {
        "pop_density": round(pd, 1),
        "household_density": round(pd / hh, 1),
        "growth_pct": round(gr, 2),
        "nearest_locality": ranked[0]["name"],
    }
