"""Road distance and travel time between two points from OSRM (OpenStreetMap routing). Used only to enrich the few top
Opportunity Finder results; scoring keeps M1's straight-line Savomart distance. Any failure returns None (the caller
records an `osrm_unavailable` flag and simply omits the road figures)."""
import httpx

from app.core import config
from app.core import opportunity_constants as C


def route(lon1: float, lat1: float, lon2: float, lat2: float) -> dict | None:
    url = f"{C.OSRM_URL.rstrip('/')}/route/v1/driving/{lon1:.6f},{lat1:.6f};{lon2:.6f},{lat2:.6f}"
    try:
        r = httpx.get(url, params={"overview": "false"}, headers={"User-Agent": config.USER_AGENT}, timeout=8)
        if r.status_code != 200:
            return None
        body = r.json()
        if body.get("code") != "Ok" or not body.get("routes"):
            return None
        rt = body["routes"][0]
        return {"distance_m": round(float(rt["distance"])), "duration_s": round(float(rt["duration"]))}
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return None
