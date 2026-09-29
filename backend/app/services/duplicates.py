"""Duplicate detection. Never merges or overwrites: it only reports candidates for a human to review.

Layers: (1) spatial, `ST_DWithin` within DUPLICATE_RADIUS_M using the GiST index as a degree-based prefilter and an
exact geography distance for the decision; (2) same normalised address + pincode.
"""
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core import property_scoring_constants as K

_SPATIAL = text("""
SELECT p.id, p.address, p.pipeline_stage,
       ST_Distance(p.location::geography, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography) AS dist
FROM properties p
WHERE ST_DWithin(p.location, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326), :deg)
  AND ST_DWithin(p.location::geography, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography, :m)
  AND (CAST(:exclude AS integer) IS NULL OR p.id <> CAST(:exclude AS integer))
ORDER BY dist
""")


def find_duplicates(db: Session, lat: float, lon: float, *, normalized_address: str | None = None,
                    pincode: str | None = None, exclude_id: int | None = None) -> list[dict]:
    out: dict[int, dict] = {}
    rows = db.execute(_SPATIAL, {"lat": lat, "lon": lon, "m": K.DUPLICATE_RADIUS_M,
                                 "deg": K.DUPLICATE_RADIUS_M / 111_000 * 1.5, "exclude": exclude_id})
    for r in rows:
        out[r.id] = {"property_id": r.id, "distance_m": round(float(r.dist), 1), "address": r.address,
                     "stage": r.pipeline_stage, "reason": "nearby"}
    if normalized_address:
        q = text("""SELECT p.id, p.address, p.pipeline_stage,
                    ST_Distance(p.location::geography, ST_SetSRID(ST_MakePoint(:lon,:lat),4326)::geography) AS dist
                    FROM properties p
                    WHERE p.normalized_address = :na AND (CAST(:pin AS text) IS NULL OR p.pincode = CAST(:pin AS text))
                      AND (CAST(:exclude AS integer) IS NULL OR p.id <> CAST(:exclude AS integer))""")
        for r in db.execute(q, {"na": normalized_address, "pin": pincode, "lat": lat, "lon": lon,
                                "exclude": exclude_id}):
            e = out.setdefault(r.id, {"property_id": r.id, "distance_m": round(float(r.dist), 1),
                                      "address": r.address, "stage": r.pipeline_stage, "reason": "same_address"})
            if e["reason"] == "nearby":
                e["reason"] = "nearby_and_same_address"
    res = sorted(out.values(), key=lambda d: d["distance_m"])
    for d in res:
        d["message"] = (f"Possible duplicate property found {d['distance_m']:.0f} m away."
                        if d["reason"].startswith("nearby") else
                        f"A property with the same address already exists ({d['distance_m']:.0f} m away).")
    return res
