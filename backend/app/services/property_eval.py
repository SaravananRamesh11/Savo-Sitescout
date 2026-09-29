"""Runs one property evaluation: gathers inputs (property + M1 lookups + public data), scores deterministically,
builds risks/recommendation, asks for a grounded explanation, and appends a NEW versioned row (never edits old ones).
"""
from datetime import datetime, timezone

from geoalchemy2.shape import to_shape
from shapely import STRtree
from shapely.geometry import Point
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core import property_scoring_constants as K
from app.models.db_models import Area, ExternalDataCache
from app.models.property_models import Property, PropertyEvaluation
from app.services import demographics, grid, m1_lookup, overpass_client, property_scoring, property_text
from app.services.pipeline import role_of  # noqa: F401  (re-exported for callers)


def _prop_dict(p: Property) -> dict:
    pt = to_shape(p.location)
    f = lambda v: None if v is None else float(v)  # noqa: E731
    return {
        "lat": pt.y, "lon": pt.x, "location_accuracy_m": p.location_accuracy_m, "location_source": p.location_source,
        "monthly_rent": f(p.monthly_rent), "expected_monthly_revenue": f(p.expected_monthly_revenue),
        "revenue_source": p.revenue_source, "total_area_sqft": p.total_area_sqft,
        "ground_floor_area_sqft": p.ground_floor_area_sqft, "sales_area_sqft": p.sales_area_sqft,
        "storage_area_sqft": p.storage_area_sqft, "frontage_ft": p.frontage_ft, "road_width_ft": p.road_width_ft,
        "visibility_score": p.visibility_score, "entry_access": p.entry_access, "exit_access": p.exit_access,
        "is_main_road_frontage": p.is_main_road_frontage, "is_corner_property": p.is_corner_property,
        "traffic_signal_nearby": p.traffic_signal_nearby, "signal_distance_m": p.signal_distance_m,
        "two_wheeler_parking": p.two_wheeler_parking, "four_wheeler_parking": p.four_wheeler_parking,
        "parking_capacity": p.parking_capacity, "parking_type": p.parking_type,
    }


def _is_organised(poi: dict) -> bool:
    if poi["cat"] == "supermarket":
        return True
    name = (poi.get("name") or "").lower()
    return poi["cat"] == "convenience" and any(b in name for b in K.ORGANISED_BRANDS)


def _poi_metrics(pois: list[dict], lat: float, lon: float) -> dict:
    x0, y0 = grid.lonlat_to_utm(lon, lat)
    here = Point(x0, y0)
    pts = []
    for p in pois:
        x, y = grid.lonlat_to_utm(p["lon"], p["lat"])
        pts.append((Point(x, y), p))
    counts = {cat: {r: 0 for r in K.DEMAND_RADII_M} for cat in K.DEMAND_CAPS_500M}
    organised = {r: 0 for r in K.COMPETITION_RADII_M}
    other = {r: 0 for r in K.COMPETITION_RADII_M}
    nearest_org = None
    tree = STRtree([p for p, _ in pts]) if pts else None
    if tree is not None:
        for i in tree.query(here.buffer(max(K.COMPETITION_RADII_M + K.DEMAND_RADII_M))):
            pt, p = pts[i]
            d = here.distance(pt)
            if p["cat"] in counts:
                for r in K.DEMAND_RADII_M:
                    if d <= r:
                        counts[p["cat"]][r] += 1
            elif p["cat"] in ("supermarket", "convenience"):
                bucket = organised if _is_organised(p) else other
                for r in K.COMPETITION_RADII_M:
                    if d <= r:
                        bucket[r] += 1
                if bucket is organised and (nearest_org is None or d < nearest_org):
                    nearest_org = d
    return {"available": True, "counts": counts, "organised": organised, "other_grocery": other,
            "nearest_organised_m": None if nearest_org is None else round(nearest_org)}


def gather_poi(db: Session, prop: Property, lat: float, lon: float) -> tuple[dict, list[dict], list[str]]:
    """Real OSM data only (cached M1 payload if it covers the point, else one small Overpass query).
    Returns (poi_metrics, data_sources, flags). Mock data is never scored."""
    sources, flags = [], []
    cache = db.query(ExternalDataCache).filter_by(area_id=prop.area_id, source="overpass").first()
    osm = None
    if cache is not None and cache.payload and cache.payload.get("pois") is not None and not cache.payload.get("mock"):
        s, w, n, e = cache.payload["bbox"]
        if s + 0.009 <= lat <= n - 0.009 and w + 0.009 <= lon <= e - 0.009:
            osm = cache.payload
            stale = cache.expires_at < datetime.now(timezone.utc)
            sources.append({"source": "OpenStreetMap via Overpass (cached from the M1 area analysis)",
                            "mocked": False, "fetched_at": cache.fetched_at.isoformat(),
                            "note": "older than the 24 h freshness window" if stale else "fresh cache"})
            if stale:
                flags.append("osm_stale_cache")
    if osm is None:
        d = 0.0125  # about 1.4 km each way
        try:
            osm = overpass_client.fetch_bbox(lat - d, lon - d, lat + d, lon + d)
            sources.append({"source": "OpenStreetMap via Overpass (live query around the property)", "mocked": False,
                            "fetched_at": osm["fetched_at"], "note": "live query"})
        except Exception as exc:  # noqa: BLE001
            flags.append("osm_unavailable")
            sources.append({"source": "OpenStreetMap via Overpass", "mocked": False, "fetched_at": None,
                            "note": f"unavailable ({str(exc)[:120]}); demand and competition left unscored"})
            return {"available": False}, sources, flags
    return _poi_metrics(osm["pois"], lat, lon), sources, flags


def _pin_distance_to_assignment(db: Session, p: Property) -> float | None:
    if p.assignment_id is None:
        return None
    from app.models.property_models import ScoutingAssignment

    a = db.get(ScoutingAssignment, p.assignment_id)
    if a is None or a.hotspot_lat is None:
        return None
    x1, y1 = grid.lonlat_to_utm(to_shape(p.location).x, to_shape(p.location).y)
    x2, y2 = grid.lonlat_to_utm(a.hotspot_lon, a.hotspot_lat)
    d = Point(x1, y1).distance(Point(x2, y2))
    return round(d) if d > K.MAX_PIN_FROM_ASSIGNMENT_M else None


def new_evaluation_row(db: Session, property_id: int, trigger: str, created_by: str) -> PropertyEvaluation:
    """Reserve the next version as a 'running' row so the UI can show progress. Filled in by evaluate_property."""
    version = (db.query(func.max(PropertyEvaluation.evaluation_version)).filter_by(property_id=property_id).scalar()
               or 0) + 1
    ev = PropertyEvaluation(property_id=property_id, evaluation_version=version, status="running", trigger=trigger,
                            created_by=created_by)
    db.add(ev)
    db.commit()
    return ev


def run_evaluation(eval_id: int) -> None:
    """Background-task entrypoint: own session, records failure instead of raising."""
    from app.core.db import get_session

    db = get_session()
    try:
        ev = db.get(PropertyEvaluation, eval_id)
        try:
            evaluate_property(db, ev.property_id, ev.trigger, ev.created_by, eval_id=eval_id)
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            ev = db.get(PropertyEvaluation, eval_id)
            ev.status, ev.error = "failed", f"{type(exc).__name__}: {exc}"[:500]
            db.commit()
    finally:
        db.close()


def evaluate_property(db: Session, property_id: int, trigger: str, created_by: str,
                      eval_id: int | None = None) -> PropertyEvaluation:
    p = db.get(Property, property_id)
    if p is None:
        raise ValueError("Property not found")
    x = _prop_dict(p)
    lat, lon = x["lat"], x["lon"]
    flags: list[str] = []
    sources: list[dict] = []

    x["m1"] = m1_lookup.m1_context(db, p.id, p.area_id)
    if x["m1"]["area_report_id"] is not None:
        sources.append({"source": f"M1 Area Fitness Report #{x['m1']['area_report_id']}", "mocked": False,
                        "fetched_at": x["m1"]["report_created_at"], "note": "latest completed report for this area"})
    else:
        flags.append("no_m1_report")
    x["poi"], s, fl = gather_poi(db, p, lat, lon)
    sources += s
    flags += fl
    d = demographics.estimate_at(lat, lon)
    x["demo"] = {**d, "mocked": True}
    flags.append("population_mocked")
    sources.append({"source": "ESTIMATED demographics (mock locality table, not official Census data)", "mocked": True,
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "note": f"nearest locality {d['nearest_locality']}"})
    sources.append({"source": "Savomart stores (operational)", "mocked": bool(x["m1"].get("stores_mocked")),
                    "fetched_at": None, "note": x["m1"].get("stores_source", "spatial distance query")})
    if x["m1"].get("stores_mocked"):
        flags.append("savomart_mocked")
    sources.append({"source": "BD Executive field capture", "mocked": False,
                    "fetched_at": p.updated_at.isoformat() if p.updated_at else None,
                    "note": f"submitted by {p.submitted_by or p.created_by}"})
    field_org = [c for c in p.competitors if c.kind in ("supermarket", "organised_grocery")]
    x["field_organised_competitors"] = len(field_org)

    result = property_scoring.score_property(x)
    extra = {"pin_far_from_assignment_m": _pin_distance_to_assignment(db, p), "duplicate_flags": p.duplicate_flags}
    risks = property_scoring.build_risks(x, result, flags, extra)
    rec, rec_text = property_scoring.recommend(result["total"], result["confidence"], risks)

    metrics = {**result["metrics"], "poi": x["poi"], "demographics": x["demo"],
               "field_competitors": [{"name": c.name, "kind": c.kind, "approx_distance_m": c.approx_distance_m}
                                     for c in p.competitors],
               "recommendation_reason": rec_text}
    insights = property_text.build_insights(x, result, metrics)
    facts = property_text.build_facts(p.id, x, result, metrics, risks, rec, flags)
    explanation, source, notes = property_text.generate_explanation(facts)
    if any(n.startswith("llm_output_rejected") for n in notes):
        flags.append("llm_output_rejected")

    ev = db.get(PropertyEvaluation, eval_id) if eval_id else new_evaluation_row(db, p.id, trigger, created_by)
    ev.status, ev.error = "completed", None
    ev.overall_score, ev.confidence = result["total"], result["confidence"]
    ev.score_breakdown, ev.insights, ev.risks, ev.recommendation = result["breakdown"], insights, risks, rec
    ev.explanation, ev.explanation_source, ev.data_sources = explanation, source, sources
    ev.data_quality_flags, ev.m1_context, ev.metrics = sorted(set(flags)), x["m1"], metrics
    db.commit()
    return ev
