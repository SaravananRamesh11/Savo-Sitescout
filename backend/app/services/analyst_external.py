"""External-data tools for the conversational analyst (read-only, registered into analyst_tools.TOOLS).

These answer questions the saved M1/M2/M3 data cannot ("what areas are in Chennai?", "where is Perungudi?",
"how many supermarkets are near Tambaram?") using the same sources the app already trusts:
  * OGD India pincode boundaries  - bundled file data/chennai_pincodes.geojson (no network)
  * Nominatim (OpenStreetMap)     - live place lookup, bounded to Chennai, 1 request/s policy via geocoding._nominatim
  * Overpass (OpenStreetMap)      - live counts of mapped amenities around a point (small query, cached in memory)
  * Bundled locality table        - ESTIMATED population density / households / growth, never presented as Census data
Census of India ward tables, the Tamil Nadu OGD portal and Bhuvan have no open API this app can call reliably, so they
are deliberately not faked. Every result names its source and basis so the answer can say how far to trust it.
"""
import time
import zlib

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core import config
from app.models.db_models import Area
from app.services import analyst_tools as T
from app.services import demographics, geocoding
from app.services.m1_lookup import latest_completed_report

OSM_HOME = "https://www.openstreetmap.org"
CATEGORIES = ("supermarket", "convenience", "school", "college", "hospital", "clinic", "bank", "transit")
_QUERIES = {  # one Overpass statement per category, in CATEGORIES order
    "supermarket": '["shop"="supermarket"]', "convenience": '["shop"="convenience"]', "school": '["amenity"="school"]',
    "college": '["amenity"~"^(college|university)$"]', "hospital": '["amenity"="hospital"]',
    "clinic": '["amenity"~"^(clinic|doctors)$"]', "bank": '["amenity"="bank"]',
    "transit": '["highway"="bus_stop"]',
}
_CACHE_S = 6 * 3600
_count_cache: dict[tuple, tuple[float, dict]] = {}


def _ext_src(label: str, href: str) -> dict:
    return {"type": "external", "id": zlib.crc32(href.encode()), "label": label, "href": href}


def _osm_link(lat: float, lon: float) -> str:
    return f"{OSM_HOME}/?mlat={lat:.5f}&mlon={lon:.5f}#map=15/{lat:.5f}/{lon:.5f}"


# ------------------------------------------------------------------ helpers
def _search_nominatim(query: str, limit: int = 3) -> list[dict]:
    vb = f"{geocoding.CHENNAI_BBOX[0]},{geocoding.CHENNAI_BBOX[3]},{geocoding.CHENNAI_BBOX[2]},{geocoding.CHENNAI_BBOX[1]}"
    return geocoding._nominatim("search", {"q": f"{query}, Chennai", "format": "jsonv2", "limit": limit,
                                           "viewbox": vb, "bounded": 1, "countrycodes": "in", "addressdetails": 1})


def _resolve_point(query: str) -> dict | None:
    """(lat, lon, name, source) for a place: the bundled locality table first (no network), then Nominatim.
    Raises geocoding.AreaResolveError when the geocoder is unreachable and the table has no match."""
    q = (query or "").strip().lower()
    if len(q) < 3:
        return None
    zones = demographics.load_mock()["zones"]
    hit = next((z for z in zones if z["name"].lower() == q), None) or next((z for z in zones if q in z["name"].lower()), None)
    if hit:
        return {"lat": hit["lat"], "lon": hit["lon"], "name": hit["name"], "source": "bundled locality table"}
    rows = _search_nominatim(query, 1)
    if not rows:
        return None
    r = rows[0]
    return {"lat": float(r["lat"]), "lon": float(r["lon"]), "name": r.get("name") or query.strip(),
            "source": "OpenStreetMap Nominatim"}


def _saved_areas_at(db: Session, lat: float, lon: float) -> list[dict]:
    rows = db.execute(text("SELECT id, resolved_name FROM areas WHERE ST_Contains(geometry, "
                           "ST_SetSRID(ST_MakePoint(:lon,:lat),4326)) ORDER BY area_km2 ASC LIMIT 3"),
                      {"lon": lon, "lat": lat}).all()
    out = []
    for r in rows:
        rep = latest_completed_report(db, r.id)
        out.append({"area_id": r.id, "area_name": r.resolved_name, "report_id": rep.id if rep else None,
                    "overall_score": rep.overall_score if rep else None})
    return out


# ------------------------------------------------------------------ tools
def list_chennai_areas(db: Session, persona: dict, query: str = "") -> dict:
    """Areas available to scout: Chennai Corporation zones and suburb areas from the OGD India pincode boundaries, plus
    the bundled named localities. All 'N more' counts are computed here so the answer never needs arithmetic."""
    q = (query or "").strip().lower()
    zones: dict[str, list[str]] = {}
    suburbs: dict[str, list[str]] = {}
    ward_pins = 0
    for pin, feat in geocoding._pincode_index().items():
        loc = geocoding._locality_from_label(feat["properties"].get("label", ""))
        if q and q not in loc.lower() and q not in pin:
            continue
        if loc.lower().startswith("ward"):
            ward_pins += 1  # labelled only by corporation ward numbers: counted, not listed as place names
        else:
            (zones if loc.lower().startswith("zone") else suburbs).setdefault(loc, []).append(pin)
    names = [z["name"] for z in demographics.load_mock()["zones"] if not q or q in z["name"].lower()]
    if not zones and not suburbs and not names and not ward_pins:
        return T._missing(f"No Chennai pincode area or locality matches '{query}'.")

    def rows(d: dict, cap: int) -> list[dict]:
        out = [{"name": k, "pincode_count": len(v), **({"pincodes": sorted(v)} if q else {})} for k, v in sorted(d.items())]
        return out[:cap]

    analysed = sorted({a.resolved_name for a in db.query(Area).all() if latest_completed_report(db, a.id) is not None})
    res = {"found": True, "source": "OGD India pincode boundaries + bundled locality table",
           "filter": query or None,
           "chennai_corporation_zones": rows(zones, 20), "chennai_corporation_zones_total": len(zones),
           "suburb_areas": rows(suburbs, 15), "suburb_areas_total": len(suburbs),
           "suburb_areas_not_shown": max(0, len(suburbs) - 15),
           "pincodes_total": sum(len(v) for v in zones.values()) + sum(len(v) for v in suburbs.values()) + ward_pins,
           "pincodes_labelled_by_ward_number_only": ward_pins,
           "named_localities": names[:20], "named_localities_total": len(names),
           "named_localities_not_shown": max(0, len(names) - 20),
           "already_analysed": analysed[:12], "already_analysed_total": len(analysed),
           "next_step": "To scout an area, run an analysis for it from the Analyse tab.",
           "sources": [_ext_src("OGD India pincode boundaries", "https://data.gov.in")]}
    return res


def lookup_place(db: Session, persona: dict, query: str) -> dict:
    """Where a place is (OpenStreetMap Nominatim, bounded to Chennai) and whether it is already analysed."""
    try:
        rows = _search_nominatim(query, 3)
    except geocoding.AreaResolveError as exc:
        return T._missing(f"The geocoder could not be reached ({exc}).")
    if not rows:
        return T._missing(f"'{query}' was not found in Chennai.")
    places, sources = [], []
    for r in rows:
        lat, lon = float(r["lat"]), float(r["lon"])
        places.append({"name": r.get("name") or query, "type": r.get("type"), "category": r.get("category"),
                       "address": ", ".join((r.get("display_name") or "").split(",")[:4]), "lat": round(lat, 5),
                       "lon": round(lon, 5), "inside_chennai_region": geocoding._in_chennai(lon, lat),
                       "saved_areas_containing_it": _saved_areas_at(db, lat, lon)})
        sources.append(_ext_src(f"OpenStreetMap: {places[-1]['name']}", _osm_link(lat, lon)))
    return {"found": True, "source": "OpenStreetMap Nominatim", "places": places, "sources": sources[:2]}


def place_demographics(db: Session, persona: dict, place: str) -> dict:
    """Estimated population density, household density and growth around a place. NOT official Census figures."""
    try:
        pt = _resolve_point(place)
    except geocoding.AreaResolveError as exc:
        return T._missing(f"The place could not be located ({exc}).")
    if pt is None:
        return T._missing(f"'{place}' was not found in Chennai.")
    est = demographics.estimate_at(pt["lat"], pt["lon"])
    return {"found": True, "place": pt["name"], "lat": round(pt["lat"], 5), "lon": round(pt["lon"], 5),
            "place_found_via": pt["source"],
            "estimate": {"population_per_km2": est["pop_density"], "households_per_km2": est["household_density"],
                         "growth_percent_per_year": est["growth_pct"], "nearest_locality_in_table": est["nearest_locality"]},
            "basis": "ESTIMATE from a bundled locality table blended by distance. Not official Census of India figures; "
                     "ward-level Census data has no open API.",
            "sources": [_ext_src(f"Estimate near {pt['name']} (not Census)", _osm_link(pt["lat"], pt["lon"]))]}


def _overpass_counts(lat: float, lon: float, radius_m: int) -> dict:
    key = (round(lat, 3), round(lon, 3), radius_m)
    hit = _count_cache.get(key)
    if hit and time.time() - hit[0] < _CACHE_S:
        return hit[1]
    body = "[out:json][timeout:25];\n" + "\n".join(
        f"nwr{_QUERIES[c]}(around:{radius_m},{lat:.6f},{lon:.6f});out count;" for c in CATEGORIES)
    last: Exception | None = None
    for endpoint in config.OVERPASS_ENDPOINTS[:2]:
        try:
            r = httpx.post(endpoint, data={"data": body}, headers={"User-Agent": config.USER_AGENT}, timeout=35)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("application/json"):
                els = [e for e in r.json().get("elements", []) if e.get("type") == "count"]
                if len(els) == len(CATEGORIES):
                    counts = {c: int(e["tags"]["total"]) for c, e in zip(CATEGORIES, els)}
                    _count_cache[key] = (time.time(), counts)
                    return counts
            last = RuntimeError(f"HTTP {r.status_code}")
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            last = exc
    raise RuntimeError(f"Overpass unavailable: {last}")


def nearby_amenities(db: Session, persona: dict, place: str, radius_m: int | None = None,
                     category: str | None = None) -> dict:
    """Counts of mapped amenities within a radius of a place (OpenStreetMap Overpass)."""
    radius = max(300, min(int(radius_m or 1000), 2000))
    try:
        pt = _resolve_point(place)
    except geocoding.AreaResolveError as exc:
        return T._missing(f"The place could not be located ({exc}).")
    if pt is None:
        return T._missing(f"'{place}' was not found in Chennai.")
    try:
        counts = _overpass_counts(pt["lat"], pt["lon"], radius)
    except RuntimeError as exc:
        return T._missing(f"The OpenStreetMap amenity service could not be reached ({exc}).", place=pt["name"])
    if category:
        counts = {category: counts[category]}
    return {"found": True, "place": pt["name"], "radius_m": radius, "counts_within_radius": counts,
            "place_found_via": pt["source"],
            "basis": "Counts of features mapped in OpenStreetMap; OSM can be incomplete, so treat them as a minimum.",
            "sources": [_ext_src(f"OpenStreetMap near {pt['name']}", _osm_link(pt["lat"], pt["lon"]))]}


_compare_saved = T.compare_areas


def compare_areas_extended(db: Session, persona: dict, areas: list[str]) -> dict:
    """Saved-report comparison when every area has one. Otherwise a PARTIAL comparison: the analysed areas keep their
    report facts, the others get the labelled demographic estimate, and nothing is ranked (no like-for-like score)."""
    res = _compare_saved(db, persona, areas)
    if res.get("found") or res.get("candidates") or not res.get("not_found_names"):
        return res
    missing = set(res["not_found_names"])
    analysed, not_analysed, sources = [], [], []
    for name in areas:
        if name in missing:
            d = place_demographics(db, persona, name)
            if d.get("found"):
                not_analysed.append({"name": name, "estimate": d["estimate"], "basis": d["basis"]})
                sources += d["sources"]
            else:
                not_analysed.append({"name": name, "estimate": None, "note": T.INSUFFICIENT})
        else:
            r = T.get_area_report(db, persona, name)
            if r.get("found"):
                analysed.append({k: r[k] for k in ("area_name", "report_id", "overall_score", "rating", "report_date", "factors")})
                sources += r["sources"]
    if not analysed and not any(x["estimate"] for x in not_analysed):
        return res
    return {"found": True, "partial": True, "analysed_areas": analysed, "not_analysed_areas": not_analysed,
            "note": "Not every area has a saved area report, so they cannot be ranked on the same score. "
                    "Analyse the missing areas from the Analyse tab for a like-for-like comparison.",
            "sources": sources}


EXTERNAL_TOOLS = {
    "list_chennai_areas": {"fn": list_chennai_areas, "args": {"query": ("str", False, None)},
                           "desc": "Chennai pincode areas and named localities available to scout (OGD India pincode boundaries); "
                                   "optional text filter. Use for 'what areas are in Chennai'."},
    "lookup_place": {"fn": lookup_place, "args": {"query": ("str", True, None)},
                     "desc": "Find where a named place is in Chennai (OpenStreetMap Nominatim) and whether it is already analysed."},
    "place_demographics": {"fn": place_demographics, "args": {"place": ("str", True, None)},
                           "desc": "ESTIMATED population density, households and growth around a place (not Census figures)."},
    "nearby_amenities": {"fn": nearby_amenities,
                         "args": {"place": ("str", True, None), "radius_m": ("int", False, None),
                                  "category": ("str", False, CATEGORIES)},
                         "desc": "Counts of mapped supermarkets, schools, hospitals, banks, bus stops etc. within radius_m "
                                 "(300-2000, default 1000) of a place (OpenStreetMap Overpass)."},
}
T.TOOLS.update(EXTERNAL_TOOLS)
T.TOOLS["compare_areas"] = {**T.TOOLS["compare_areas"], "fn": compare_areas_extended}
