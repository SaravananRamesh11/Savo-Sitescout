"""Two-layer validation: drafts accept partial data; submission requires the essentials and returns per-field errors.

Errors block submission. Warnings need acknowledgement but do not block.
"""
import re
from typing import Any

from app.core import property_scoring_constants as K

CHENNAI_BBOX = (79.90, 12.70, 80.45, 13.35)  # minlon, minlat, maxlon, maxlat (same as geocoding.py)
REQUIRED_FIELDS = {
    "address": "Address",
    "locality": "Locality",
    "pincode": "Pincode",
    "monthly_rent": "Monthly rent",
    "total_area_sqft": "Total usable area",
    "ground_floor_area_sqft": "Ground-floor area",
    "frontage_ft": "Frontage",
    "floor": "Floor",
    "number_of_floors": "Number of floors",
    "property_type": "Property type",
    "rent_negotiable": "Rent negotiable",
    "is_main_road_frontage": "Main-road frontage",
    "is_corner_property": "Corner property",
    "entry_access": "Entry access",
    "exit_access": "Exit access",
    "two_wheeler_parking": "Two-wheeler parking",
    "four_wheeler_parking": "Four-wheeler parking",
}
PROPERTY_TYPES = {"shop", "showroom", "standalone", "mall_unit", "other"}
PHOTO_LABELS = {"front_view": "front view", "road_view": "road view", "interior_view": "interior view",
                "side_view": "side view", "parking_view": "parking view", "building_condition": "building condition"}


def normalize_address(text: str | None) -> str | None:
    if not text:
        return None
    t = re.sub(r"[^\w\s]", " ", text.lower())
    t = re.sub(r"\b(no|door|plot|flat)\b\.?", " ", t)
    t = re.sub(r"\bst\b", "street", t)
    t = re.sub(r"\brd\b", "road", t)
    t = re.sub(r"\bmain\b", "main", t)
    return re.sub(r"\s+", " ", t).strip() or None


def in_chennai(lon: float, lat: float) -> bool:
    return CHENNAI_BBOX[0] <= lon <= CHENNAI_BBOX[2] and CHENNAI_BBOX[1] <= lat <= CHENNAI_BBOX[3]


def _num(v: Any) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def plausibility(d: dict) -> tuple[list[dict], list[dict]]:
    """Checks that apply to drafts and submissions alike. Returns (errors, warnings)."""
    errors: list[dict] = []
    warnings: list[dict] = []

    def err(field, msg):
        errors.append({"field": field, "message": msg})

    def warn(field, msg):
        warnings.append({"field": field, "message": msg})

    total, ground = _num(d.get("total_area_sqft")), _num(d.get("ground_floor_area_sqft"))
    sales, storage = _num(d.get("sales_area_sqft")), _num(d.get("storage_area_sqft"))
    rent = _num(d.get("monthly_rent"))
    for f, label in (("total_area_sqft", "Total area"), ("ground_floor_area_sqft", "Ground-floor area"),
                     ("frontage_ft", "Frontage"), ("monthly_rent", "Monthly rent")):
        v = _num(d.get(f))
        if v is not None and v <= 0:
            err(f, f"{label} must be greater than zero.")
    if total and ground and ground > total:
        err("ground_floor_area_sqft", "Ground-floor area cannot be larger than the total area.")
    if total and sales and storage and sales + storage > total * 1.001:
        err("sales_area_sqft", "Sales area plus storage area cannot exceed the total area.")
    elif total and sales and sales > total:
        err("sales_area_sqft", "Sales area cannot be larger than the total area.")
    pin = d.get("pincode")
    if pin not in (None, "") and not re.fullmatch(r"\d{6}", str(pin)):
        err("pincode", "Pincode must be 6 digits.")
    elif pin and not str(pin).startswith("600"):
        warn("pincode", "This pincode is outside the Chennai 600xxx range. Check it.")
    vis = _num(d.get("visibility_score"))
    if vis is not None and not 1 <= vis <= 5:
        err("visibility_score", "Visibility must be between 1 and 5.")
    lat, lon = _num(d.get("lat")), _num(d.get("lon"))
    if lat is not None and lon is not None and not in_chennai(lon, lat):
        err("location", "This location is outside Chennai. Move the pin onto the property.")
    if rent and rent > 0 and not K.MONTHLY_RENT_SANITY[0] <= rent <= K.MONTHLY_RENT_SANITY[1]:
        warn("monthly_rent", f"Rent Rs {rent:,.0f} looks unusual. Check that it is a monthly figure.")
    if rent and total and total > 0:
        psf = rent / total
        lo, hi = K.RENT_PER_SQFT_SANITY
        if not lo <= psf <= hi:
            warn("monthly_rent", f"Rent per sq ft (Rs {psf:,.0f}) looks unusual for this area size. Check rent and area.")
    acc = _num(d.get("location_accuracy_m"))
    if acc is not None and acc > K.POOR_GPS_ACCURACY_M:
        warn("location", f"GPS accuracy is only about {acc:,.0f} m. Drag the pin onto the property to be exact.")
    if d.get("traffic_signal_nearby") is False and _num(d.get("signal_distance_m")) is not None:
        warn("signal_distance_m", "A signal distance was entered but no traffic signal is marked nearby.")
    return errors, warnings


def validate_for_submission(d: dict, photo_types: set[str]) -> tuple[list[dict], list[dict]]:
    errors, warnings = plausibility(d)
    have = {e["field"] for e in errors}
    if d.get("lat") is None or d.get("lon") is None:
        errors.append({"field": "location", "message": "Location is required. Use the current location or place the pin on the map."})
    for f, label in REQUIRED_FIELDS.items():
        v = d.get(f)
        if f in have:
            continue
        if v is None or (isinstance(v, str) and not v.strip()):
            errors.append({"field": f, "message": f"{label} is required before submitting this property."})
    if d.get("property_type") and d["property_type"] not in PROPERTY_TYPES:
        errors.append({"field": "property_type", "message": "Choose a valid property type."})
    for t in K.REQUIRED_PHOTO_TYPES:
        if t not in photo_types:
            label = PHOTO_LABELS.get(t, t)
            art = "An" if label[0] in "aeiou" else "A"
            errors.append({"field": f"photo:{t}", "message": f"{art} {label} photo is required before submitting."})
    return errors, warnings
