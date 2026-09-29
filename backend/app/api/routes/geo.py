from fastapi import APIRouter, Depends, HTTPException

from app.core.personas import current_persona
from app.services import geocoding

router = APIRouter(tags=["geo"])


@router.get("/geo/reverse")
def reverse(lat: float, lon: float, _persona: dict = Depends(current_persona)):
    """Reverse geocode to prefill address / locality / pincode (Nominatim, shared 1 req/s limiter). Optional."""
    if not geocoding._in_chennai(lon, lat):
        raise HTTPException(422, "This location is outside Chennai.")
    try:
        row = geocoding._nominatim("reverse", {"lat": lat, "lon": lon, "format": "jsonv2", "zoom": 18,
                                               "addressdetails": 1})
    except geocoding.AreaResolveError as exc:
        raise HTTPException(503, str(exc))
    a = (row or {}).get("address", {}) if isinstance(row, dict) else {}
    locality = (a.get("neighbourhood") or a.get("quarter") or a.get("residential") or a.get("suburb")
                or a.get("city_district"))
    road = a.get("road")
    parts = [x for x in (a.get("house_number"), road, locality) if x]
    return {"address": ", ".join(parts) or (row or {}).get("display_name"), "locality": locality,
            "pincode": a.get("postcode"), "road": road, "house_number": a.get("house_number")}
