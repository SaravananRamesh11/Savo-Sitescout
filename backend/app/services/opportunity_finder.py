"""Opportunity Finder orchestrator: where should the BD team scout next?

    tiles (cached Overpass) -> per-cell features (M1 code) -> fresh Savomart distances -> scouting coverage (M2/M3)
    -> opportunity score -> eligibility -> top-N (M1 hotspot spreading) -> details

Deterministic, no LLM. Runs as a background job; progress is written to the run row for the page to poll.
"""
import time
from datetime import datetime, timezone

import numpy as np
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core import opportunity_constants as C
from app.core.db import get_session
from app.models.opportunity_models import OpportunityRun, OpportunityTile
from app.services import (features, grid, hotspots, opportunity_coverage as cov_mod, opportunity_tiles as tiles,
                          osrm_client, savomart_client, scoring)
from app.services.opportunity_score import score_cell

POP_FLAG_TEXT = ("Population is an ESTIMATE from a bundled locality table (Census-2011 order of magnitude), "
                 "not official or current Census data.")
REASONS = {"tile_missing": "No map data could be fetched for this area.",
           "no_mapped_features": "No mapped roads or places here (likely water or open land).",
           "outside_demographic_table": "Too far from any locality in the demographic table for a reliable estimate."}
_FEATURE_OUT = ["pop_density", "household_density", "growth_pct", "road_km_per_km2", "major_road_dist_m",
                "transit_count", "commercial_per_km2", "amenity_units_per_km2", "competitor_count", "savomart_dist_m"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ run lifecycle
def active_run(db: Session) -> OpportunityRun | None:
    """A queued/running run that is not stale (a crashed process must not block new runs forever)."""
    now = _now()
    for r in db.scalars(select(OpportunityRun).where(OpportunityRun.status.in_(("queued", "running")))
                        .order_by(OpportunityRun.id.desc())):
        if (now - r.created_at).total_seconds() < C.RUN_STALE_MINUTES * 60:
            return r
    return None


def create_run(db: Session, persona_id: str) -> OpportunityRun:
    run = OpportunityRun(status="queued", created_by=persona_id, config=C.config_snapshot(),
                         progress={"phase": "queued", "done": 0, "total": 0, "message": "Waiting to start"})
    db.add(run)
    db.commit()
    keep = [r for r in db.scalars(select(OpportunityRun.id).order_by(OpportunityRun.id.desc()).limit(C.KEEP_RUNS))]
    db.execute(delete(OpportunityRun).where(OpportunityRun.id.not_in(keep)))
    db.commit()
    return run


def _progress(db: Session, run: OpportunityRun, phase: str, done: int, total: int, message: str) -> None:
    run.progress = {"phase": phase, "done": done, "total": total, "message": message}
    db.commit()


# ------------------------------------------------------------------ pure helpers (unit-testable)
def eligibility(f: dict | None) -> str | None:
    """None if the cell may be ranked, otherwise the reason code. Never lets an estimate stand in for missing land."""
    if f is None:
        return "tile_missing"
    if not f.get("evidence"):
        return "no_mapped_features"
    if f.get("demo_dist_km", 0) > C.MAX_DEMO_DISTANCE_KM:
        return "outside_demographic_table"
    return None


def cell_centre_ll(col: int, row: int) -> tuple[float, float]:
    lon, lat = grid._to_ll.transform((col + 0.5) * grid.CELL_SIZE_M, (row + 0.5) * grid.CELL_SIZE_M)
    return lat, lon


def rank_cells(scores: dict[str, dict], n: int | None = None) -> list[str]:
    """Top-N cells: chosen spread out (no touching neighbours where possible) by the routine M1 uses for hotspots, then
    numbered strictly by score, so rank 1 is always the highest score."""
    picked = hotspots.pick_hotspots({cid: {"cell_id": cid, "col": s["col"], "row": s["row"], "score": s["score"],
                                           "coverage": 1.0} for cid, s in scores.items()}, n or C.TOP_N)
    return sorted(picked, key=lambda cid: scores[cid]["score"], reverse=True)


def risks_for(f: dict, m1_breakdown: list[dict], tile_meta: dict | None) -> list[str]:
    out = []
    if tile_meta and tile_meta.get("status") == "stale":
        out.append(f"Map data for this area is from an older cache ({(tile_meta.get('fetched_at') or '')[:10]}).")
    weak = sorted((b for b in m1_breakdown if b["norm"] < 0.25), key=lambda b: b["norm"])[:2]
    out += [f"Weak {b['label'].lower()}: {b['explanation']}" for b in weak]
    out.append(POP_FLAG_TEXT)
    return out


def cell_corners_ll(cells: list[tuple[int, int]]) -> list[list[list[float]]]:
    """Four lon/lat corners (SW, SE, NE, NW) per cell, transformed in one vectorised call (compact map payload)."""
    if not cells:
        return []
    cs = np.array(cells, dtype=float) * grid.CELL_SIZE_M
    xs = np.stack([cs[:, 0], cs[:, 0] + 500, cs[:, 0] + 500, cs[:, 0]], axis=1)
    ys = np.stack([cs[:, 1], cs[:, 1], cs[:, 1] + 500, cs[:, 1] + 500], axis=1)
    lon, lat = grid._to_ll.transform(xs.ravel(), ys.ravel())
    lon, lat = np.round(lon.reshape(-1, 4), 5), np.round(lat.reshape(-1, 4), 5)
    return [[[float(lon[i, j]), float(lat[i, j])] for j in range(4)] for i in range(len(cells))]


# ------------------------------------------------------------------ details
def nearest_three(rec_lat: float, rec_lon: float, stores: list[dict]) -> list[dict]:
    if not stores:
        return []
    x, y = grid.lonlat_to_utm(rec_lon, rec_lat)
    d = sorted(((float(np.hypot(x - sx, y - sy)), s) for s in stores
                for sx, sy in [grid.lonlat_to_utm(s["lon"], s["lat"])]), key=lambda t: t[0])
    return [{"name": s["name"], "lat": s["lat"], "lon": s["lon"], "distance_m": round(dist)} for dist, s in d[:3]]


def build_detail(db: Session, run: OpportunityRun, rec: dict, with_road: bool = False) -> dict:
    """Everything the detail sheet shows for one cell, derived from the stored record (deterministic)."""
    f = rec["f"]
    coverage_entry = (run.summary.get("coverage") or {}).get(rec["id"])
    scored = score_cell(f, rec["cov"])
    city_break = run.summary.get("city_break") or []
    m1_breakdown = [b for b in scored["breakdown"] if b["key"] != "unscouted_opportunity"]
    # why_bullets/risks read the unscaled M1 factors so they match M1's wording exactly
    m1_unscaled = scoring.score_features(f)["breakdown"]
    tile_meta = (run.summary.get("tiles") or {}).get(tiles.tile_of_cell(rec["col"], rec["row"]))
    row = db.scalar(select(OpportunityTile).where(OpportunityTile.tile_key == tiles.tile_of_cell(rec["col"], rec["row"])))
    places = (row.payload.get("places") if row else None) or []
    locality = hotspots.nearest_locality(rec["lat"], rec["lon"], places, f.get("demo_locality") or "Chennai")
    stores = nearest_three(rec["lat"], rec["lon"], run.summary.get("stores") or [])
    flags = []
    if with_road and stores:
        rt = osrm_client.route(rec["lon"], rec["lat"], stores[0]["lon"], stores[0]["lat"])
        if rt:
            stores[0]["road_distance_m"], stores[0]["road_duration_s"] = rt["distance_m"], rt["duration_s"]
        else:
            flags.append("osrm_unavailable")
    return {"cell_id": rec["id"], "lat": rec["lat"], "lon": rec["lon"], "locality": locality,
            "opportunity_score": scored["total"], "m1_score": scored["m1_score"], "rating": scored["rating"],
            "breakdown": scored["breakdown"], "w_unscouted": scored["w"],
            "positives": hotspots.why_bullets(m1_unscaled, city_break, 3) if city_break else [],
            "risks": risks_for(f, m1_breakdown, tile_meta), "flags": flags,
            "features": {"competitors_within_1km": f["competitor_count"], "transit_stops_within_500m": f["transit_count"],
                         "population_per_km2": f["pop_density"], "households_per_km2": f["household_density"],
                         "population_growth_pct": f["growth_pct"], "road_km_per_km2": f["road_km_per_km2"],
                         "nearest_major_road_m": f["major_road_dist_m"], "nearest_named_road": f.get("nearest_named_road"),
                         "mapped_in_this_cell": f.get("counts", {})},
            "nearest_stores": stores,
            "scouting": {"coverage": rec["cov"], "summary": cov_mod.describe(coverage_entry),
                         "signals": (coverage_entry or {}).get("signals", [])},
            "map_data": {"status": (tile_meta or {}).get("status"), "fetched_at": (tile_meta or {}).get("fetched_at")}}


# ------------------------------------------------------------------ the run
def run_finder(run_id: int, refresh: bool = False) -> None:
    db = get_session()
    try:
        run = db.get(OpportunityRun, run_id)
        run.status = "running"
        _progress(db, run, "tiles", 0, 1, "Preparing the city grid")
        _execute(db, run, refresh)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        run = db.get(OpportunityRun, run_id)
        run.status, run.error = "failed", str(exc)[:500]
        run.completed_at = _now()
        db.commit()
    finally:
        db.close()


def _execute(db: Session, run: OpportunityRun, refresh: bool) -> None:
    tile_cells = tiles.city_tiles()
    keys = sorted(tile_cells)
    stores_rows, store_meta = savomart_client.get_stores(db)
    payloads: dict[str, dict] = {}
    tile_meta: dict[str, dict] = {}
    failures, breaker = 0, False
    cached_rows = tiles.load_all(db)
    for i, key in enumerate(keys):
        cached = cached_rows.get(key)
        fresh_hit = cached is not None and not refresh and cached.expires_at > _now()
        if not fresh_hit or i % 6 == 0:  # progress is saved when real work is happening, and every few cached tiles
            _progress(db, run, "tiles", i, len(keys), f"Reading map data: area {i + 1} of {len(keys)}")
        attempt = not breaker
        payload, meta = tiles.get_tile(db, key, refresh=refresh, allow_fetch=attempt, cached=cached)
        tile_meta[key] = meta
        if payload is not None:
            payloads[key] = payload
        if meta["status"] == "live":
            failures = 0
            time.sleep(C.PAUSE_BETWEEN_TILES_S)
        elif meta["status"] in ("missing", "stale") and attempt:
            failures += 1
            if failures >= C.MAX_CONSECUTIVE_TILE_FAILURES:
                breaker = True  # Overpass is unhappy: stop hammering it, use what the cache has

    _progress(db, run, "scoring", len(keys), len(keys), "Scoring 500 m cells and checking scouting coverage")
    all_cells = [c for k in keys for c in tile_cells[k]]
    near = tiles.nearest_stores(all_cells, stores_rows)
    coverage = cov_mod.compute_coverage(db)

    recs: dict[str, dict] = {}
    scores: dict[str, dict] = {}
    feats_for_avg: dict[str, dict] = {}
    for key in keys:
        payload = payloads.get(key)
        for col, row in tile_cells[key]:
            cid = grid.make_cell_id(col, row)
            f = dict(payload["cells"][cid]) if payload and cid in payload["cells"] else None
            reason = eligibility(f)
            if f is None:
                lat, lon = cell_centre_ll(col, row)
                recs[cid] = {"id": cid, "col": col, "row": row, "lat": round(lat, 5), "lon": round(lon, 5), "ok": False,
                             "why": reason, "opp": None, "m1": None, "cov": 0.0, "f": None}
                continue
            dist, name = near.get((col, row), (None, None))
            f["savomart_dist_m"], f["nearest_savomart"] = (round(dist, 1) if dist is not None else None), name
            cov = (coverage.get(cid) or {}).get("coverage", 0.0)
            rec = {"id": cid, "col": col, "row": row, "lat": f["lat"], "lon": f["lon"], "ok": reason is None,
                   "why": reason, "opp": None, "m1": None, "cov": cov, "f": f}
            if reason is None:
                sc = score_cell(f, cov)
                rec["opp"], rec["m1"] = sc["total"], sc["m1_score"]
                scores[cid] = {"col": col, "row": row, "score": sc["total"]}
                feats_for_avg[cid] = f
            recs[cid] = rec

    city_break = (scoring.score_features(features.aggregate_area(feats_for_avg))["breakdown"]
                  if feats_for_avg else [])
    cov_map = {cid: v for cid, v in coverage.items() if v["units"] > 0}
    stores_compact = [{"name": s["name"], "lat": s["latitude"], "lon": s["longitude"]} for s in stores_rows]
    statuses = [m["status"] for m in tile_meta.values()]
    by_reason: dict[str, int] = {}
    for r in recs.values():
        if not r["ok"]:
            by_reason[r["why"]] = by_reason.get(r["why"], 0) + 1
    flags = {"population_mocked"}
    if "stale" in statuses:
        flags.add("osm_stale_cache")
    if "missing" in statuses:
        flags.add("osm_tile_missing")
    if breaker:
        flags.add("overpass_unavailable")
    if store_meta.get("flag"):
        flags.add(store_meta["flag"].split(" (")[0])  # same trimming M1 uses: the flag name only, never the raw error
    fetched = sorted(m["fetched_at"] for m in tile_meta.values() if m.get("fetched_at"))
    run.summary = {"cells_total": len(recs), "cells_ranked": len(scores), "unranked_by_reason": by_reason,
                   "tiles_total": len(keys), "tiles_missing": statuses.count("missing"),
                   "tiles_stale": statuses.count("stale"), "tiles_live": statuses.count("live"),
                   "tiles_from_cache": statuses.count("fresh"), "tiles": {k: {"status": m["status"], "fetched_at": m["fetched_at"]}
                                                                          for k, m in tile_meta.items()},
                   "city_break": city_break, "stores": stores_compact, "coverage": cov_map,
                   "score_range": [min(s["score"] for s in scores.values()), max(s["score"] for s in scores.values())]
                   if scores else None}
    run.cells = list(recs.values())
    run.data_quality_flags = sorted(flags)
    run.data_sources = [
        {"source": "OpenStreetMap via Overpass API (fetched in 6 km tiles)", "url": "https://overpass-api.de", "mocked": False,
         "fetched_at": fetched[-1] if fetched else None, "oldest_tile_fetched_at": fetched[0] if fetched else None,
         "note": f"{statuses.count('live')} fetched now, {statuses.count('fresh')} from cache, {statuses.count('stale')} stale, "
                 f"{statuses.count('missing')} missing"},
        {"source": store_meta.get("source", "Savomart stores"), "url": "Savomart Stores API",
         "mocked": bool(store_meta.get("mocked")), "fetched_at": store_meta.get("fetched_at")},
        {"source": "ESTIMATED demographics (bundled locality table, Census-2011 order of magnitude; not official)",
         "url": "https://censusindia.gov.in", "mocked": True, "fetched_at": None},
        {"source": "Savo SiteScout M2/M3 records (properties, assignments, catchment studies)", "url": "internal",
         "mocked": False, "fetched_at": _now().isoformat()}]
    db.commit()

    _progress(db, run, "details", len(keys), len(keys), "Preparing the top opportunities")
    top_ids = rank_cells(scores)
    by_id = {r["id"]: r for r in run.cells}
    top, road_failed = [], False
    for rank, cid in enumerate(top_ids, start=1):
        d = build_detail(db, run, by_id[cid], with_road=True)
        d["rank"] = rank
        road_failed = road_failed or "osrm_unavailable" in d["flags"]
        top.append(d)
    if any(t["nearest_stores"] and "road_distance_m" in t["nearest_stores"][0] for t in top):
        run.data_sources = run.data_sources + [{"source": "OSRM road routing (top results only)", "url": C.OSRM_URL,
                                                "mocked": False, "fetched_at": _now().isoformat()}]
    if road_failed:
        run.data_quality_flags = sorted(set(run.data_quality_flags) | {"osrm_unavailable"})
    run.top = top
    run.status, run.completed_at = "completed", _now()
    run.progress = {"phase": "done", "done": len(keys), "total": len(keys), "message": "Done"}
    db.commit()
