"""The 9 agent nodes. Each takes (state, db) and returns a partial state update.

External-data nodes never crash the run: they fall back to stale cache, then labelled mock data,
and record a `data_quality_flags` entry + a `data_sources` entry saying exactly what was used.
"""
from datetime import datetime, timedelta, timezone

from geoalchemy2.shape import from_shape, to_shape
from shapely import wkt
from shapely.geometry import Point
from sqlalchemy import select

from app.core.config import CELL_SIZE_M, HOTSPOT_COUNT, OVERPASS_CACHE_HOURS
from app.models.db_models import Area, AreaReport, ExternalDataCache, GridCell
from app.services import demographics, features, geocoding, grid, hotspots, mock_osm, overpass_client
from app.services import report_text, savomart_client
from app.services.scoring import score_features

CELL_KM2 = (CELL_SIZE_M / 1000) ** 2


def _now():
    return datetime.now(timezone.utc)


def _add_flag(state, flag):
    if flag not in state["flags"]:
        state["flags"].append(flag)


# ----------------------------------------------------------------------------- 1 resolve_area
def resolve_area(state, db):
    area = db.get(Area, state["area_id"])
    poly = to_shape(area.geometry)
    cells = grid.cells_for_polygon(grid.to_utm(poly))
    if not cells:
        raise ValueError("The area contains no analysable grid cells")
    if area.boundary_quality != "exact":
        _add_flag(state, "area_boundary_approximate")
    state["notes"]["resolve_area"] = f"{area.resolved_name}: {len(cells)} cells of {CELL_SIZE_M} m, {area.area_km2:.1f} km²"
    return {"area_name": area.resolved_name, "input_type": area.input_type, "boundary_quality": area.boundary_quality,
            "polygon_wkt": poly.wkt, "area_km2": area.area_km2, "cells": cells}


# ----------------------------------------------------------------------------- 2 get_osm_data
def _padded_bbox(poly_ll, pad_m=1000):
    u = grid.to_utm(poly_ll).buffer(pad_m)
    minx, miny, maxx, maxy = u.bounds
    w, s = grid._to_ll.transform(minx, miny)
    e, n = grid._to_ll.transform(maxx, maxy)
    return s, w, n, e


def _cache_get(db, area_id, source):
    return db.scalar(select(ExternalDataCache).where(ExternalDataCache.area_id == area_id,
                                                     ExternalDataCache.source == source))


def _cache_put(db, area_id, source, payload, ttl_hours):
    row = _cache_get(db, area_id, source)
    now = _now()
    if row is None:
        row = ExternalDataCache(area_id=area_id, source=source)
        db.add(row)
    row.payload, row.fetched_at, row.expires_at = payload, now, now + timedelta(hours=ttl_hours)
    db.commit()


def get_osm_data(state, db):
    poly = wkt.loads(state["polygon_wkt"])
    bbox = _padded_bbox(poly)
    area_id = state["area_id"]
    cached = _cache_get(db, area_id, "overpass")
    src = {"source": "OpenStreetMap via Overpass API", "url": "https://overpass-api.de", "mocked": False}
    if cached and cached.expires_at > _now():
        osm, src["fetched_at"], src["note"] = cached.payload, cached.fetched_at.isoformat(), "fresh cache"
    else:
        try:
            osm = overpass_client.fetch_bbox(*bbox)
            _cache_put(db, area_id, "overpass", osm, OVERPASS_CACHE_HOURS)
            src["fetched_at"], src["note"] = osm["fetched_at"], "live query"
        except Exception as exc:  # noqa: BLE001
            if cached:
                osm = cached.payload
                src["fetched_at"], src["note"] = cached.fetched_at.isoformat(), f"stale cache (Overpass failed: {exc})"
                _add_flag(state, "osm_stale_cache")
            else:
                osm = mock_osm.mock_osm_for_bbox(*bbox)
                src.update(mocked=True, fetched_at=_now().isoformat(), source="MOCK OSM (synthetic)",
                           note=f"Overpass unavailable: {str(exc)[:160]}")
                _add_flag(state, "osm_mocked")
    state["sources"].append(src)
    state["notes"]["get_osm_data"] = (f"{len(osm['pois'])} POIs, {len(osm['roads'])} road segments ({src['note']})")
    return {"osm": osm}


# ----------------------------------------------------------------------------- 3 get_demographics
def get_demographics(state, db):
    demo = {}
    for col, row, _cov in state["cells"]:
        c = grid.cell_box_utm(col, row).centroid
        lon, lat = grid._to_ll.transform(c.x, c.y)
        demo[grid.make_cell_id(col, row)] = demographics.estimate_at(lat, lon)
    _cache_put(db, state["area_id"], "population", {"cells": demo, "mocked": True}, 24 * 30)
    _add_flag(state, "population_mocked")
    state["sources"].append({
        "source": "ESTIMATED demographics (mock locality table, Census-2011 order of magnitude)",
        "url": "https://censusindia.gov.in", "mocked": True, "fetched_at": _now().isoformat(),
        "note": "Ward-level Census data unavailable via API; figures are estimates, not official"})
    state["notes"]["get_demographics"] = f"{len(demo)} cells estimated (labelled mock)"
    return {"demographics": demo}


# ----------------------------------------------------------------------------- 4 get_savomart
def get_savomart(state, db):
    stores, meta = savomart_client.get_stores(db)
    if meta["flag"]:
        _add_flag(state, meta["flag"].split(" (")[0])
    state["sources"].append({"source": "Savomart Stores API (operational stores)", "mocked": meta["mocked"],
                             "fetched_at": meta["fetched_at"], "url": "internal-service.savomart.in",
                             "note": meta["source"] + (f"; {meta['flag']}" if meta["flag"] else "")})
    state["notes"]["get_savomart"] = f"{len(stores)} operational stores ({meta['source']})"
    return {"stores": stores}


# ----------------------------------------------------------------------------- 5 calculate_features
def calculate_features(state, db):
    poly = wkt.loads(state["polygon_wkt"])
    feats, totals = features.compute_cell_features(poly, state["cells"], state["osm"], state["stores"],
                                                   state["demographics"])
    agg = features.aggregate_area(feats)
    poly_utm = grid.to_utm(poly)
    centroid = poly_utm.centroid
    inside = []
    dists = []
    for s in state["stores"]:
        x, y = grid.lonlat_to_utm(s["longitude"], s["latitude"])
        p = Point(x, y)
        d = float(p.distance(centroid))
        dists.append((d, s))
        if poly_utm.contains(p):
            inside.append(s["name"])
    nearest = min(dists, key=lambda t: t[0]) if dists else None
    km2 = state["area_km2"]
    est_pop = sum(f["pop_density"] * f["coverage"] * CELL_KM2 for f in feats.values())
    est_hh = sum(f["household_density"] * f["coverage"] * CELL_KM2 for f in feats.values())
    profile = {
        "area_km2": round(km2, 2), "grid_cells": len(feats),
        "estimated_population": round(est_pop, -2), "estimated_households": round(est_hh, -2),
        "population_is_estimate": True,
        "schools": totals.get("school", 0), "colleges": totals.get("college", 0),
        "hospitals": totals.get("hospital", 0), "clinics": totals.get("clinic", 0),
        "supermarkets": totals.get("supermarket", 0), "convenience_stores": totals.get("convenience", 0),
        "shops_offices_banks": totals.get("commercial", 0) + totals.get("supermarket", 0) + totals.get("convenience", 0),
        "transit_stops": totals.get("transit", 0),
        "road_km": totals.get("road_km", 0), "major_road_km": totals.get("major_road_km", 0),
        "savomart_stores_inside": len(inside), "savomart_store_names_inside": inside,
        "nearest_savomart_name": nearest[1]["name"] if nearest else None,
        "nearest_savomart_m": round(nearest[0]) if nearest else None,
        "savomart_stores_within_3km": len([1 for d, _ in dists if d <= 3000]),
    }
    state["notes"]["calculate_features"] = f"{len(feats)} cells binned; {totals.get('commercial', 0)} commercial POIs inside"
    return {"cell_features": feats, "area_features": agg, "area_totals": totals, "profile": profile}


# ----------------------------------------------------------------------------- 6 calculate_score
def calculate_score(state, db):
    cell_scores = {}
    for cid, f in state["cell_features"].items():
        sc = score_features(f)
        cell_scores[cid] = {"cell_id": cid, "col": f["col"], "row": f["row"], "coverage": f["coverage"],
                            "score": sc["total"], "breakdown": sc["breakdown"]}
    area_score = score_features(state["area_features"])
    state["notes"]["calculate_score"] = f"area score {area_score['total']} ({area_score['rating']})"
    return {"cell_scores": cell_scores, "area_score": area_score}


# ----------------------------------------------------------------------------- 7 identify_hotspots
def identify_hotspots(state, db):
    ids = hotspots.pick_hotspots(state["cell_scores"], HOTSPOT_COUNT)
    places = state["osm"].get("places", [])
    out = []
    for rank, cid in enumerate(ids, 1):
        f, sc = state["cell_features"][cid], state["cell_scores"][cid]
        out.append({
            "rank": rank, "cell_id": cid, "score": sc["score"], "lat": round(f["lat"], 6), "lon": round(f["lon"], 6),
            "locality": hotspots.nearest_locality(f["lat"], f["lon"], places, state["area_name"]),
            "nearest_named_road": f["nearest_named_road"] if (f["nearest_named_road_m"] or 1e9) <= 600 else None,
            "why": hotspots.why_bullets(sc["breakdown"], state["area_score"]["breakdown"]),
        })
    state["notes"]["identify_hotspots"] = f"top {len(out)} cells selected"
    return {"hotspot_ids": ids, "hotspots": out}


# ----------------------------------------------------------------------------- 8 generate_report
def generate_report(state, db):
    area = {"name": state["area_name"], "input_type": state["input_type"], "area_km2": round(state["area_km2"], 2),
            "boundary_quality": state["boundary_quality"]}
    facts = report_text.build_facts(area, state["profile"], state["area_score"], state["hotspots"], state["flags"])
    expl, source, notes = report_text.generate_explanation(facts)
    for n in notes:
        if n.startswith("llm_output_rejected"):
            _add_flag(state, "llm_output_rejected")
    state["notes"]["generate_report"] = f"explanation by {source}" + (f" ({'; '.join(notes)})" if notes else "")
    return {"facts": facts, "explanation": expl, "explanation_source": source}


# ----------------------------------------------------------------------------- 9 save_report
def save_report(state, db):
    report = db.get(AreaReport, state["report_id"])
    db.query(GridCell).filter(GridCell.report_id == report.id).delete()
    hot = {h["cell_id"]: h for h in state["hotspots"]}
    for cid, sc in state["cell_scores"].items():
        f = state["cell_features"][cid]
        h = hot.get(cid)
        db.add(GridCell(
            report_id=report.id, area_id=state["area_id"], cell_id=cid,
            geometry=from_shape(grid.cell_polygon_ll(sc["col"], sc["row"]), srid=4326),
            centroid=from_shape(Point(f["lon"], f["lat"]), srid=4326),
            coverage=sc["coverage"], score=sc["score"], score_breakdown=sc["breakdown"],
            features={k: v for k, v in f.items() if k not in ("lat", "lon")},
            is_hotspot=h is not None, hotspot_rank=h["rank"] if h else None,
            locality_name=h["locality"] if h else None, nearest_named_road=h["nearest_named_road"] if h else None,
            why=h["why"] if h else None))
    report.overall_score = state["area_score"]["total"]
    report.rating = state["area_score"]["rating"]
    report.score_breakdown = state["area_score"]["breakdown"]
    report.area_profile = {**state["profile"], "hotspots": state["hotspots"], "facts": state["facts"]}
    report.data_quality_flags = state["flags"]
    report.data_sources = state["sources"]
    report.llm_explanation = state["explanation"]
    report.explanation_source = state["explanation_source"]
    db.commit()
    state["notes"]["save_report"] = f"{len(state['cell_scores'])} grid cells stored"
    return {}


NODES = [
    ("resolve_area", "Resolve area", resolve_area),
    ("get_osm_data", "Fetch OpenStreetMap data", get_osm_data),
    ("get_demographics", "Estimate demographics", get_demographics),
    ("get_savomart", "Load Savomart stores", get_savomart),
    ("calculate_features", "Compute grid features", calculate_features),
    ("calculate_score", "Score the area", calculate_score),
    ("identify_hotspots", "Find scouting hotspots", identify_hotspots),
    ("generate_report", "Write grounded explanation", generate_report),
    ("save_report", "Save report", save_report),
]
