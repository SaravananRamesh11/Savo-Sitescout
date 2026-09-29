"""Reuse of M1 data for a property. Nothing from M1 is copied into the property: everything is looked up by
spatial query against the latest completed AreaReport for the property's area."""
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.db_models import AreaReport

_CELL = text("""
SELECT c.cell_id, c.score, c.is_hotspot, c.hotspot_rank, c.locality_name, c.score_breakdown, c.features, c.coverage,
       ST_Contains(c.geometry, p.location) AS inside,
       ST_Distance(c.geometry::geography, p.location::geography) AS dist
FROM grid_cells c, properties p
WHERE p.id = :pid AND c.report_id = :rid
ORDER BY ST_Contains(c.geometry, p.location) DESC, dist ASC
LIMIT 1
""")

_NEAREST_STORE = text("""
SELECT s.name, s.external_store_id, ST_Distance(s.geometry::geography, p.location::geography) AS dist
FROM savomart_stores s, properties p
WHERE p.id = :pid AND s.is_operational
ORDER BY dist ASC LIMIT 6
""")


def latest_completed_report(db: Session, area_id: int) -> AreaReport | None:
    return (db.query(AreaReport).filter(AreaReport.area_id == area_id, AreaReport.status == "completed")
            .order_by(AreaReport.created_at.desc()).first())


def m1_context(db: Session, property_id: int, area_id: int) -> dict:
    """Area report + containing grid cell + hotspot status + nearest Savomart stores. Missing pieces are None."""
    ctx: dict = {"area_report_id": None, "area_score": None, "area_rating": None, "report_created_at": None,
                 "cell_id": None, "cell_score": None, "cell_inside": None, "is_hotspot": None, "hotspot_rank": None,
                 "cell_locality": None, "report_flags": [], "nearest_savomart": None, "nearest_savomart_m": None,
                 "savomart_within_1km": 0}
    rep = latest_completed_report(db, area_id)
    if rep is not None:
        ctx.update(area_report_id=rep.id, area_score=rep.overall_score, area_rating=rep.rating,
                   report_created_at=rep.created_at.isoformat(), report_flags=rep.data_quality_flags or [])
        row = db.execute(_CELL, {"pid": property_id, "rid": rep.id}).first()
        if row is not None:
            ctx.update(cell_id=row.cell_id, cell_score=row.score, cell_inside=bool(row.inside),
                       is_hotspot=bool(row.is_hotspot), hotspot_rank=row.hotspot_rank, cell_locality=row.locality_name)
    stores = db.execute(_NEAREST_STORE, {"pid": property_id}).all()
    if stores:
        ctx["nearest_savomart"], ctx["nearest_savomart_m"] = stores[0].name, round(float(stores[0].dist))
        ctx["savomart_within_1km"] = len([s for s in stores if s.dist <= 1000])
        ctx["stores_source"] = "savomart_stores table"
    else:
        # The table is only filled by a successful live Stores API call. Fall back to the same chain M1 uses
        # (API -> saved snapshot) and measure in UTM metres.
        from geoalchemy2.shape import to_shape
        from shapely.geometry import Point
        from sqlalchemy import select

        from app.models.property_models import Property
        from app.services import grid, savomart_client

        rows, meta = savomart_client.get_stores(db)
        pt = to_shape(db.scalar(select(Property.location).where(Property.id == property_id)))
        x0, y0 = grid.lonlat_to_utm(pt.x, pt.y)
        ds = sorted(((Point(*grid.lonlat_to_utm(r["longitude"], r["latitude"])).distance(Point(x0, y0)), r["name"])
                     for r in rows))
        if ds:
            ctx["nearest_savomart"], ctx["nearest_savomart_m"] = ds[0][1], round(ds[0][0])
            ctx["savomart_within_1km"] = len([1 for d, _ in ds if d <= 1000])
        ctx["stores_source"] = meta["source"]
        ctx["stores_mocked"] = bool(meta.get("mocked"))
    return ctx
