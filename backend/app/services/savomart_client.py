"""Savomart internal Stores API client + normalised local cache (savomart_stores).

Reports read stores from the DB table (reproducible, fast PostGIS queries, survives API
outages); the API is the source of truth and refreshes the table on a TTL.
Note: the endpoint answers GET (the PDF's `curl --data ''` turns the request into a POST,
which this service rejects with 405).
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from geoalchemy2.shape import from_shape
from shapely.geometry import Point
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import SAVOMART_CRON_TOKEN, SAVOMART_STORES_URL, STORES_TTL_HOURS
from app.models.db_models import SavomartStore

SNAPSHOT = Path(__file__).resolve().parents[1] / "mock_data" / "savomart_stores_snapshot.json"


class StoresApiError(RuntimeError):
    pass


def fetch_live() -> list[dict]:
    if not SAVOMART_CRON_TOKEN:
        raise StoresApiError("SAVOMART_CRON_TOKEN is not set")
    try:
        r = httpx.get(SAVOMART_STORES_URL, params={"is_operational": "True"},
                      headers={"X-cron-token": SAVOMART_CRON_TOKEN}, timeout=30, follow_redirects=True)
    except httpx.HTTPError as exc:
        raise StoresApiError(f"Stores API unreachable: {exc}") from exc
    if r.status_code != 200:
        raise StoresApiError(f"Stores API HTTP {r.status_code}")
    rows = r.json().get("data") or []
    out = []
    for s in rows:
        g = s.get("geocoordinates") or {}
        if g.get("latitude") is None or g.get("longitude") is None:
            continue
        out.append({
            "store_code": s["store_code"], "name": s.get("name") or s["store_code"], "address": s.get("address"),
            "zone": s.get("zone"), "latitude": float(g["latitude"]), "longitude": float(g["longitude"]),
            "is_operational": bool(s.get("is_operational", True)),
        })
    if not out:
        raise StoresApiError("Stores API returned no usable stores")
    return out


def upsert_stores(db: Session, rows: list[dict]) -> None:
    now = datetime.now(timezone.utc)
    existing = {s.external_store_id: s for s in db.scalars(select(SavomartStore))}
    for r in rows:
        geom = from_shape(Point(r["longitude"], r["latitude"]), srid=4326)
        s = existing.get(r["store_code"])
        if s is None:
            s = SavomartStore(external_store_id=r["store_code"])
            db.add(s)
        s.name, s.address, s.zone = r["name"], r.get("address"), r.get("zone")
        s.geometry, s.is_operational, s.fetched_at = geom, r["is_operational"], now
    db.commit()


def store_rows(db: Session, only_operational: bool = True) -> list[dict]:
    from geoalchemy2.shape import to_shape

    q = select(SavomartStore)
    if only_operational:
        q = q.where(SavomartStore.is_operational.is_(True))
    out = []
    for s in db.scalars(q):
        p = to_shape(s.geometry)
        out.append({"store_code": s.external_store_id, "name": s.name, "zone": s.zone,
                    "latitude": p.y, "longitude": p.x, "fetched_at": s.fetched_at.isoformat()})
    return out


def get_stores(db: Session, force_refresh: bool = False) -> tuple[list[dict], dict]:
    """Returns (stores, meta). meta = {source, fetched_at, mocked, flag}."""
    newest = db.scalar(select(SavomartStore.fetched_at).order_by(SavomartStore.fetched_at.desc()).limit(1))
    fresh = newest is not None and (datetime.now(timezone.utc) - newest) < timedelta(hours=STORES_TTL_HOURS)
    if force_refresh or not fresh:
        try:
            upsert_stores(db, fetch_live())
            rows = store_rows(db)
            return rows, {"source": "savomart_stores_api", "fetched_at": datetime.now(timezone.utc).isoformat(),
                          "mocked": False, "flag": None}
        except Exception as exc:  # noqa: BLE001 - fall back rather than crash the report
            db.rollback()
            err = str(exc)
        rows = store_rows(db)
        if rows:
            return rows, {"source": "savomart_stores_db_cache", "fetched_at": rows[0]["fetched_at"],
                          "mocked": False, "flag": f"savomart_stale ({err})"}
        snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        return snap["stores"], {"source": "savomart_stores_snapshot", "fetched_at": None, "mocked": True,
                                "flag": f"savomart_mocked ({err})"}
    rows = store_rows(db)
    return rows, {"source": "savomart_stores_db_cache", "fetched_at": rows[0]["fetched_at"] if rows else None,
                  "mocked": False, "flag": None}
