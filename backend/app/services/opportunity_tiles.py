"""City-wide data for the Opportunity Finder, fetched and cached one 6 km tile at a time (never per grid cell).

Chennai-wide in ONE Overpass query is far too heavy (tens of MB against a 90 s server limit), so the city is cut into
12 x 12-cell tiles aligned to the same 500 m lattice M1 uses. For each tile we make one padded Overpass query with the
existing overpass_client, compute the per-cell features with the existing features.compute_cell_features (same maths as
M1), and cache the *derived* per-cell features plus the tile's place names. Raw road geometry is not stored.
If Overpass fails we fall back to a stale cache; otherwise the tile is reported missing. Nothing is ever mocked.
Savomart distances are deliberately left out of the cache (stores change) and added fresh on every run.
"""
from datetime import datetime, timedelta, timezone

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.nodes.pipeline import _padded_bbox
from app.core import opportunity_constants as C
from app.models.opportunity_models import OpportunityTile
from app.services import demographics, features, grid, overpass_client

_FEATURE_FIELDS = ["pop_density", "household_density", "growth_pct", "road_km_per_km2", "major_road_dist_m",
                   "transit_count", "commercial_per_km2", "amenity_units_per_km2", "competitor_count"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def tile_of_cell(col: int, row: int) -> str:
    """Cache key. The tile size is part of it, so changing OPP_TILE_CELLS can never reuse tiles cut differently."""
    return f"{C.TILE_CELLS}:{col // C.TILE_CELLS}_{row // C.TILE_CELLS}"


def full_tile_cells(key: str) -> list[tuple[int, int]]:
    """Every cell of a tile. The cache always holds the WHOLE tile, whatever bounds the current run uses, so a tile
    cached under one bbox is complete for any other."""
    size, rest = key.split(":")
    n = int(size)
    tc, tr = (int(x) for x in rest.split("_"))
    return [(tc * n + i, tr * n + j) for i in range(n) for j in range(n)]


def city_tiles(bbox: tuple | None = None) -> dict[str, list[tuple[int, int]]]:
    """{tile_key: [(col, row), ...]} for every cell inside the city bounds, grouped by lattice-aligned tile."""
    cells = grid.cells_in_bbox_ll(*(bbox or C.city_bbox()), limit=60000)
    tiles: dict[str, list[tuple[int, int]]] = {}
    for col, row in cells:
        tiles.setdefault(tile_of_cell(col, row), []).append((col, row))
    return tiles


def _zone_arrays():
    zs = demographics.load_mock()["zones"]
    return np.array([z["lat"] for z in zs]), np.array([z["lon"] for z in zs])


def _demo_distance_km(lat: float, lon: float, zlat, zlon) -> float:
    p = np.pi / 180
    a = (np.sin((zlat - lat) * p / 2) ** 2 + np.cos(lat * p) * np.cos(zlat * p) * np.sin((zlon - lon) * p / 2) ** 2)
    return float((12742 * np.arcsin(np.sqrt(a))).min())


def compute_tile(cells: list[tuple[int, int]], osm: dict) -> dict:
    """Derived per-cell features for one tile from its (padded) OSM data, using the M1 feature code unchanged."""
    poly = grid.union_of_cells_ll(cells)
    feats, _totals = features.compute_cell_features(poly, [(c, r, 1.0) for c, r in cells], osm, [])
    zlat, zlon = _zone_arrays()
    out = {}
    for cid, f in feats.items():
        rec = {k: (round(f[k], 3) if isinstance(f[k], float) else f[k]) for k in _FEATURE_FIELDS}
        rec.update(col=f["col"], row=f["row"], lat=round(f["lat"], 5), lon=round(f["lon"], 5), coverage=1.0,
                   counts=f["counts"], demo_locality=f["demo_locality"], nearest_named_road=f["nearest_named_road"],
                   demo_dist_km=round(_demo_distance_km(f["lat"], f["lon"], zlat, zlon), 2),
                   evidence=bool(f["road_km_per_km2"] > 0 or sum(f["counts"].values()) > 0))
        out[cid] = rec
    return {"cells": out, "places": osm.get("places", []),
            "counts": {"pois": len(osm["pois"]), "roads": len(osm["roads"]), "places": len(osm.get("places", []))}}


def _fetch(key: str) -> tuple[dict, dict]:
    cells = full_tile_cells(key)
    poly = grid.union_of_cells_ll(cells)
    osm = overpass_client.fetch_bbox(*_padded_bbox(poly, C.TILE_PAD_M))
    return compute_tile(cells, osm), osm


_LOAD = object()


def load_all(db: Session) -> dict[str, OpportunityTile]:
    """Every cached tile in one query (the database is remote: one round trip instead of one per tile)."""
    return {r.tile_key: r for r in db.scalars(select(OpportunityTile))}


def get_tile(db: Session, key: str, refresh: bool = False, allow_fetch: bool = True,
             cached=_LOAD) -> tuple[dict | None, dict]:
    """(payload | None, meta{status: fresh|live|stale|missing, fetched_at, endpoint, error}). One Overpass call at most.
    `cached` lets a caller that already loaded the tiles (load_all) skip the per-tile query."""
    row = (db.scalar(select(OpportunityTile).where(OpportunityTile.tile_key == key)) if cached is _LOAD else cached)
    now = _now()
    if row is not None and row.expires_at > now and not refresh:
        return row.payload, {"status": "fresh", "fetched_at": row.fetched_at.isoformat(), "endpoint": row.endpoint}
    error = "live fetch not attempted"
    if allow_fetch:
        try:
            payload, osm = _fetch(key)
            if row is None:
                row = OpportunityTile(tile_key=key)
                db.add(row)
            row.payload, row.endpoint = payload, osm.get("endpoint")
            row.fetched_at, row.expires_at = now, now + timedelta(hours=C.CACHE_HOURS)
            db.commit()
            return payload, {"status": "live", "fetched_at": now.isoformat(), "endpoint": row.endpoint}
        except Exception as exc:  # noqa: BLE001  (Overpass can fail in many ways; the fallback is the same)
            db.rollback()
            error = str(exc)[:200]
            row = db.scalar(select(OpportunityTile).where(OpportunityTile.tile_key == key))
    if row is not None:
        return row.payload, {"status": "stale", "fetched_at": row.fetched_at.isoformat(), "endpoint": row.endpoint,
                             "error": error}
    return None, {"status": "missing", "fetched_at": None, "endpoint": None, "error": error}


def nearest_stores(cells: list[tuple[int, int]], stores: list[dict]) -> dict[tuple[int, int], tuple[float, str]]:
    """Straight-line (UTM metres) distance and name of the nearest store for each cell centre: the same measure M1 uses,
    recomputed every run from the current stores (vectorised)."""
    if not stores or not cells:
        return {}
    pts = np.array([grid.lonlat_to_utm(s["longitude"], s["latitude"]) for s in stores])
    cx = (np.array([c for c, _ in cells]) + 0.5) * grid.CELL_SIZE_M
    cy = (np.array([r for _, r in cells]) + 0.5) * grid.CELL_SIZE_M
    d = np.sqrt((cx[:, None] - pts[None, :, 0]) ** 2 + (cy[:, None] - pts[None, :, 1]) ** 2)
    idx = d.argmin(axis=1)
    return {cell: (float(d[i, idx[i]]), stores[int(idx[i])]["name"]) for i, cell in enumerate(cells)}
