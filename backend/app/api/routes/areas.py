import json

from fastapi import APIRouter, Depends, HTTPException, Query
from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import mapping
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.db_models import Area, SavomartStore
from app.models.schemas import ResolveRequest
from app.services import geocoding, grid, savomart_client

router = APIRouter(tags=["areas"])


def area_json(a: Area) -> dict:
    return {"area_id": a.id, "name": a.resolved_name, "input_type": a.input_type, "raw_input": a.raw_input,
            "geometry": mapping(to_shape(a.geometry)), "area_km2": round(a.area_km2, 2),
            "boundary_quality": a.boundary_quality, "created_at": a.created_at.isoformat()}


@router.post("/areas/resolve")
def resolve(req: ResolveRequest, db: Session = Depends(get_db)):
    try:
        if req.type == "pincode":
            if not isinstance(req.value, str):
                raise geocoding.AreaResolveError("Pincode must be a string")
            key = req.value.strip()
            existing = db.scalar(select(Area).where(Area.input_type == "pincode", Area.raw_input == key))
            if existing:
                return {**area_json(existing), "note": "resolved earlier (cached geometry)"}
            r = geocoding.resolve_pincode(key)
        elif req.type == "name":
            if not isinstance(req.value, str):
                raise geocoding.AreaResolveError("Locality name must be a string")
            key = " ".join(req.value.lower().split())
            existing = db.scalar(select(Area).where(Area.input_type == "name", Area.raw_input == key))
            if existing:
                return {**area_json(existing), "note": "resolved earlier (cached geometry)"}
            r = geocoding.resolve_name(req.value)
        else:
            ids = req.value if isinstance(req.value, list) else [req.value]
            r = geocoding.resolve_cells(ids)
            key = r["raw_input"]
            existing = db.scalar(select(Area).where(Area.input_type == "grid_cells", Area.raw_input == key))
            if existing:
                return {**area_json(existing), "note": "resolved earlier (cached geometry)"}
        km2 = geocoding.validate_size(r["polygon"])
    except geocoding.AreaResolveError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    area = Area(input_type=req.type, raw_input=key, resolved_name=r["name"], area_km2=km2,
                geometry=from_shape(r["polygon"], srid=4326), boundary_quality=r["quality"])
    db.add(area)
    db.commit()
    return {**area_json(area), "note": r["note"]}


@router.get("/areas/{area_id}")
def get_area(area_id: int, db: Session = Depends(get_db)):
    a = db.get(Area, area_id)
    if not a:
        raise HTTPException(404, "Area not found")
    return area_json(a)


@router.get("/pincodes")
def pincodes():
    """Chennai pincodes (from the local boundary file) for search suggestions."""
    idx = geocoding._pincode_index()
    return [{"pincode": p, "label": geocoding._locality_from_label(f["properties"].get("label", "")),
             "centroid": f["properties"].get("centroid")} for p, f in sorted(idx.items())]


@router.get("/grid")
def grid_geojson(bbox: str = Query(..., description="minlon,minlat,maxlon,maxlat")):
    try:
        minlon, minlat, maxlon, maxlat = [float(x) for x in bbox.split(",")]
        cells = grid.cells_in_bbox_ll(minlon, minlat, maxlon, maxlat)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "id": grid.make_cell_id(c, r), "properties": {"cell_id": grid.make_cell_id(c, r)},
         "geometry": mapping(grid.cell_polygon_ll(c, r))} for c, r in cells]}


@router.get("/stores")
def stores(refresh: bool = False, db: Session = Depends(get_db)):
    rows, meta = savomart_client.get_stores(db, force_refresh=refresh)
    return {"meta": meta, "stores": rows}
