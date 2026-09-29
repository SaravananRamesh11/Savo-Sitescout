"""One bulk Overpass query per area bounding box (never one per grid cell)."""
import time
from datetime import datetime, timezone

import httpx

from app.core.config import OVERPASS_ENDPOINTS, OVERPASS_TIMEOUT_S, USER_AGENT

ROAD_CLASSES = (
    "motorway|trunk|primary|secondary|tertiary|unclassified|residential|living_street|"
    "motorway_link|trunk_link|primary_link|secondary_link"
)
MAJOR_ROADS = {"motorway", "trunk", "primary", "secondary", "motorway_link", "trunk_link", "primary_link",
               "secondary_link"}


class OverpassError(RuntimeError):
    pass


def build_query(south: float, west: float, north: float, east: float) -> str:
    bb = f"({south:.6f},{west:.6f},{north:.6f},{east:.6f})"
    return f"""[out:json][timeout:{OVERPASS_TIMEOUT_S}];
(
  nwr["amenity"~"^(school|college|university|hospital|clinic|doctors|bank|marketplace)$"]{bb};
  nwr["shop"]{bb};
  nwr["office"]{bb};
  nwr["highway"="bus_stop"]{bb};
  nwr["public_transport"~"^(platform|station)$"]{bb};
  nwr["railway"~"^(station|halt|subway_entrance|tram_stop)$"]{bb};
  node["place"~"^(suburb|neighbourhood|quarter|locality|village|town)$"]{bb};
)->.p;
.p out center tags;
way["highway"~"^({ROAD_CLASSES})$"]{bb};
out geom tags qt;"""


def classify(tags: dict) -> str | None:
    amenity, shop = tags.get("amenity"), tags.get("shop")
    if amenity == "school":
        return "school"
    if amenity in ("college", "university"):
        return "college"
    if amenity == "hospital":
        return "hospital"
    if amenity in ("clinic", "doctors"):
        return "clinic"
    if shop == "supermarket":
        return "supermarket"
    if shop == "convenience":
        return "convenience"
    if shop or tags.get("office") or amenity in ("bank", "marketplace"):
        return "commercial"
    if (tags.get("highway") == "bus_stop" or tags.get("public_transport") in ("platform", "station")
            or tags.get("railway") in ("station", "halt", "subway_entrance", "tram_stop")):
        return "transit"
    return None


def parse(elements: list[dict]) -> dict:
    pois, roads, places = [], [], []
    for el in elements:
        tags = el.get("tags") or {}
        if el["type"] == "way" and "geometry" in el and tags.get("highway") and not (
            tags.get("shop") or tags.get("amenity")
        ):
            coords = [[p["lon"], p["lat"]] for p in el["geometry"]]
            if len(coords) >= 2:
                roads.append({"coords": coords, "hw": tags["highway"], "name": tags.get("name")})
            continue
        lon = el.get("lon") or (el.get("center") or {}).get("lon")
        lat = el.get("lat") or (el.get("center") or {}).get("lat")
        if lon is None or lat is None:
            continue
        if tags.get("place") and el["type"] == "node":
            if tags.get("name"):
                places.append({"lon": lon, "lat": lat, "name": tags["name"], "place": tags["place"]})
            continue
        cat = classify(tags)
        if cat:
            pois.append({"lon": lon, "lat": lat, "cat": cat, "name": tags.get("name")})
    return {"pois": pois, "roads": roads, "places": places}


def fetch_bbox(south: float, west: float, north: float, east: float) -> dict:
    """POST the bulk query. The primary endpoint gets a patient timeout and a retry on 429/5xx; the
    mirrors are tried with a short timeout so a dead mirror can never stall a report for minutes.
    Raises OverpassError if everything fails (the caller then falls back to cache / labelled mock)."""
    query = build_query(south, west, north, east)
    last: Exception | None = None
    plan = []
    for i, endpoint in enumerate(OVERPASS_ENDPOINTS):
        plan.append((endpoint, 75 if i == 0 else 25))
    plan.insert(1, (OVERPASS_ENDPOINTS[0], 75))  # one more attempt on the primary after a short pause
    for n, (endpoint, timeout) in enumerate(plan):
        if n == 1:
            time.sleep(4)
        try:
            with httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}) as c:
                r = c.post(endpoint, data={"data": query})
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("application/json"):
                body = r.json()
                if "remark" in body and not body.get("elements"):
                    raise OverpassError(str(body["remark"])[:200])
                out = parse(body.get("elements", []))
                out["endpoint"] = endpoint
                out["fetched_at"] = datetime.now(timezone.utc).isoformat()
                out["bbox"] = [south, west, north, east]
                return out
            last = OverpassError(f"{endpoint} -> HTTP {r.status_code}")
        except Exception as exc:  # noqa: BLE001
            last = exc
    raise OverpassError(f"All Overpass endpoints failed: {last}")
