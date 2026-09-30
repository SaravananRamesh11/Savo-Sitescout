"""Opportunity Finder against the real database, with Overpass, the stores API and OSRM faked (nothing leaves the machine).

Safety: the test bbox is in Hyderabad, so its tile keys can never collide with cached Chennai tiles, and run pruning is
switched off so the tests cannot delete a run made by a user. Everything the tests create is deleted afterwards.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from geoalchemy2.shape import from_shape
from shapely.geometry import Point, box
from sqlalchemy import text

from app.core import config, opportunity_constants as C
from app.services import grid, opportunity_finder as finder, opportunity_tiles as tiles, overpass_client, osrm_client, savomart_client

from tests.test_property_integration import MGR  # noqa: E402

pytestmark = pytest.mark.skipif(not config.DB_URL, reason="database url not configured")

BBOX = (78.470, 17.380, 78.500, 17.410)  # ~3 km x 3.3 km in Hyderabad: a few tiles, far from any Chennai tile
STORES = [{"name": "Test store", "latitude": 17.395, "longitude": 78.485, "store_code": "T1", "zone": "z", "fetched_at": None}]


def fake_osm(south, west, north, east):
    lat, lon = (south + north) / 2, (west + east) / 2
    pois = [{"lon": lon + dx, "lat": lat + dy, "cat": cat, "name": None} for dx, dy, cat in
            [(0, 0, "school"), (0.001, 0.001, "supermarket"), (-0.002, 0.001, "commercial"), (0.002, -0.002, "transit"),
             (0.003, 0.002, "convenience"), (-0.003, -0.003, "hospital")]]
    roads = [{"coords": [[west, south + (north - south) * j / 12], [east, south + (north - south) * j / 12]],
              "hw": "primary" if j % 4 == 0 else "residential", "name": "Test Road" if j % 4 == 0 else None} for j in range(13)]
    roads += [{"coords": [[west + (east - west) * i / 12, south], [west + (east - west) * i / 12, north]],
               "hw": "residential", "name": None} for i in range(13)]  # a street grid across the whole tile
    places = [{"lon": west + (east - west) * i / 4, "lat": south + (north - south) * j / 4, "name": "Testpuram", "place": "suburb"}
              for i in range(5) for j in range(5)]  # real OSM has place nodes all over a 6 km tile
    return {"pois": pois, "roads": roads, "places": places, "endpoint": "fake", "fetched_at": datetime.now(timezone.utc).isoformat(),
            "bbox": [south, west, north, east]}


@pytest.fixture()
def env(monkeypatch):
    from app.core.db import get_session
    from app.main import app

    monkeypatch.setattr(C, "city_bbox", lambda: BBOX)
    monkeypatch.setattr(C, "KEEP_RUNS", 10_000)  # never prune runs that belong to a user
    monkeypatch.setattr(C, "MAX_DEMO_DISTANCE_KM", 10_000.0)  # Hyderabad is far from the Chennai demographic table
    monkeypatch.setattr(C, "PAUSE_BETWEEN_TILES_S", 0.0)
    monkeypatch.setattr(savomart_client, "get_stores", lambda db, force_refresh=False: (STORES, {
        "source": "test stores", "fetched_at": None, "mocked": False, "flag": None}))
    monkeypatch.setattr(osrm_client, "route", lambda *a: {"distance_m": 2100, "duration_s": 420})
    calls = []

    def counting(*bbox):
        calls.append(bbox)
        return fake_osm(*bbox)

    monkeypatch.setattr(overpass_client, "fetch_bbox", counting)
    db = get_session()
    made = {"runs": [], "properties": [], "areas": []}
    tile_keys = list(tiles.city_tiles(BBOX))
    db.execute(text("delete from opportunity_tiles where tile_key = any(:k)"), {"k": tile_keys})
    db.commit()
    yield TestClient(app), db, calls, made, tile_keys
    db.rollback()
    if made["runs"]:
        db.execute(text("delete from opportunity_runs where id = any(:i)"), {"i": made["runs"]})
    if made["properties"]:
        db.execute(text("delete from property_evaluations where property_id = any(:i)"), {"i": made["properties"]})
        db.execute(text("delete from property_status_history where property_id = any(:i)"), {"i": made["properties"]})
        db.execute(text("delete from properties where id = any(:i)"), {"i": made["properties"]})
    if made["areas"]:
        db.execute(text("delete from areas where id = any(:i)"), {"i": made["areas"]})
    db.execute(text("delete from opportunity_tiles where tile_key = any(:k)"), {"k": tile_keys})
    db.commit()
    db.close()


def _run(client, made, **params):
    r = client.post("/api/opportunities/runs", params=params, headers=MGR)
    assert r.status_code == 202, r.text
    made["runs"].append(r.json()["run_id"])
    return r.json()["run_id"]


def test_full_run_caching_ranking_and_details(env):
    client, db, calls, made, tile_keys = env
    rid = _run(client, made)
    st = client.get(f"/api/opportunities/runs/{rid}/status", headers=MGR).json()
    assert st["status"] == "completed", st
    assert len(calls) == len(tile_keys) >= 2  # exactly one Overpass call per tile, never per cell
    cached = db.execute(text("select payload from opportunity_tiles where tile_key = :k"), {"k": tile_keys[0]}).scalar()
    assert len(cached["cells"]) == C.TILE_CELLS ** 2  # the whole tile is cached, not just the cells inside this bbox

    run = client.get(f"/api/opportunities/runs/{rid}", headers=MGR).json()
    n_cells = len(grid.cells_in_bbox_ll(*BBOX))
    assert len(run["cells"]) == n_cells == run["summary"]["cells_total"]
    assert all(len(c["p"]) == 4 for c in run["cells"])
    assert run["summary"]["tiles_live"] == len(tile_keys) and run["summary"]["tiles_from_cache"] == 0
    assert 1 <= len(run["top"]) <= C.TOP_N and [t["rank"] for t in run["top"]] == list(range(1, len(run["top"]) + 1))
    scores = [t["opportunity_score"] for t in run["top"]]
    assert scores == sorted(scores, reverse=True)
    top = run["top"][0]
    assert top["locality"] == "Testpuram"  # from the OSM place node cached with the tile, not Nominatim
    assert top["breakdown"][-1]["key"] == "unscouted_opportunity" and top["positives"] and top["risks"]
    assert top["nearest_stores"][0]["name"] == "Test store" and top["nearest_stores"][0]["road_duration_s"] == 420
    assert top["scouting"]["coverage"] == 0 and "No recent" in top["scouting"]["summary"]
    assert "population_mocked" in run["data_quality_flags"]
    assert any("ESTIMATED" in s["source"] for s in run["data_sources"])
    assert run["config"]["w_unscouted"] == C.W_UNSCOUTED

    # any cell can be opened, not just the top ones
    other = next(c["id"] for c in run["cells"] if c["o"] is not None and c["id"] not in {t["cell_id"] for t in run["top"]})
    d = client.get(f"/api/opportunities/runs/{rid}/cells/{other}", headers=MGR).json()
    assert d["ranked"] and d["locality"] == "Testpuram" and d["opportunity_score"] > 0
    assert "road_duration_s" not in d["nearest_stores"][0]  # road time is for the top results only
    assert client.get(f"/api/opportunities/runs/{rid}/cells/9_9", headers=MGR).status_code == 404

    # second run: zero Overpass calls (cache), identical scores (deterministic)
    n_before = len(calls)
    rid2 = _run(client, made)
    run2 = client.get(f"/api/opportunities/runs/{rid2}", headers=MGR).json()
    assert len(calls) == n_before and run2["summary"]["tiles_from_cache"] == len(tile_keys)
    assert [c["o"] for c in run2["cells"]] == [c["o"] for c in run["cells"]]
    # ?refresh=true refetches every tile
    rid3 = _run(client, made, refresh=True)
    assert len(calls) == n_before + len(tile_keys) and client.get(f"/api/opportunities/runs/{rid3}/status", headers=MGR).json()["status"] == "completed"

    latest = client.get("/api/opportunities/runs/latest", headers=MGR).json()
    assert latest["run"]["run_id"] == rid3 and latest["active"] is None


def test_scouting_lowers_priority_of_the_scouted_cell_but_not_the_rest(env):
    from app.models.db_models import Area
    from app.models.property_models import Property

    client, db, calls, made, tile_keys = env
    rid = _run(client, made)
    before = {c["id"]: c for c in client.get(f"/api/opportunities/runs/{rid}", headers=MGR).json()["cells"]}
    target = next(t["cell_id"] for t in client.get(f"/api/opportunities/runs/{rid}", headers=MGR).json()["top"][:1])
    col, row = grid.parse_cell_id(target)
    lon, lat = grid._to_ll.transform((col + 0.5) * 500, (row + 0.5) * 500)

    tag = uuid.uuid4().hex[:8]
    area = Area(input_type="grid_cells", raw_input=f"opp_{tag}", resolved_name=f"Opp test {tag}", area_km2=1.0,
                geometry=from_shape(box(78.47, 17.38, 78.48, 17.39), srid=4326))
    db.add(area)
    db.flush()
    made["areas"].append(area.id)
    p = Property(area_id=area.id, created_by="bd_executive:ravi", location=from_shape(Point(lon, lat), srid=4326),
                 pipeline_stage="SUBMITTED", address=f"Opp test {tag}", submitted_at=datetime.now(timezone.utc),
                 duplicate_flags=[])
    db.add(p)
    db.flush()
    made["properties"].append(p.id)
    db.commit()

    rid2 = _run(client, made)
    after = {c["id"]: c for c in client.get(f"/api/opportunities/runs/{rid2}", headers=MGR).json()["cells"]}
    assert after[target]["c"] > 0 and after[target]["o"] < before[target]["o"]  # scouted: lower, but still scored (not excluded)
    assert after[target]["o"] is not None
    d = client.get(f"/api/opportunities/runs/{rid2}/cells/{target}", headers=MGR).json()
    assert "1 property" in d["scouting"]["summary"] and d["scouting"]["signals"][0]["kind"] == "property"
    far = next(c for c in before if before[c]["o"] is not None and max(
        abs(grid.parse_cell_id(c)[0] - col), abs(grid.parse_cell_id(c)[1] - row)) > 2)  # beyond the 8 neighbours
    assert after[far]["o"] == before[far]["o"]  # cells away from it are untouched


def test_missing_tile_is_flagged_and_unranked_not_invented_and_stale_cache_is_used(env, monkeypatch):
    client, db, calls, made, tile_keys = env
    first = _run(client, made)  # fills the cache
    assert client.get(f"/api/opportunities/runs/{first}/status", headers=MGR).json()["status"] == "completed"

    # expire every tile and make Overpass fail: the stale cache is used and flagged
    db.execute(text("update opportunity_tiles set expires_at = now() - interval '1 day' where tile_key = any(:k)"), {"k": tile_keys})
    db.commit()
    monkeypatch.setattr(overpass_client, "fetch_bbox", lambda *a: (_ for _ in ()).throw(overpass_client.OverpassError("down")))
    rid = _run(client, made)
    run = client.get(f"/api/opportunities/runs/{rid}", headers=MGR).json()
    assert "osm_stale_cache" in run["data_quality_flags"] and run["summary"]["tiles_stale"] >= 1
    assert any(t["opportunity_score"] for t in run["top"])

    # no cache at all + Overpass down: missing tiles, cells unranked with a reason, flag raised, nothing mocked
    db.execute(text("delete from opportunity_tiles where tile_key = any(:k)"), {"k": tile_keys})
    db.commit()
    rid = _run(client, made)
    run = client.get(f"/api/opportunities/runs/{rid}", headers=MGR).json()
    assert run["status"] == "completed" and run["top"] == []
    assert "osm_tile_missing" in run["data_quality_flags"] and run["summary"]["cells_ranked"] == 0
    assert {c["w"] for c in run["cells"]} == {"tile_missing"} and all(c["o"] is None for c in run["cells"])
    assert run["summary"]["unranked_by_reason"] == {"tile_missing": run["summary"]["cells_total"]}
    cid = run["cells"][0]["id"]
    d = client.get(f"/api/opportunities/runs/{rid}/cells/{cid}", headers=MGR).json()
    assert d["ranked"] is False and d["reason"] == "tile_missing" and d["reason_text"]


def test_cells_without_mapped_features_are_not_ranked(env, monkeypatch):
    client, db, calls, made, tile_keys = env
    monkeypatch.setattr(overpass_client, "fetch_bbox", lambda s, w, n, e: {
        "pois": [], "roads": [], "places": [], "endpoint": "fake", "fetched_at": datetime.now(timezone.utc).isoformat(),
        "bbox": [s, w, n, e]})
    rid = _run(client, made)
    run = client.get(f"/api/opportunities/runs/{rid}", headers=MGR).json()
    assert run["top"] == [] and {c["w"] for c in run["cells"]} == {"no_mapped_features"}  # empty land is never scored


def test_permissions_and_joining_a_running_run(env):
    from app.models.opportunity_models import OpportunityRun

    client, db, calls, made, tile_keys = env
    for persona in ("bd_executive:ravi", "survey_manager:meena", "survey_executive:karthik"):
        h = {"X-Persona": persona}
        assert client.post("/api/opportunities/runs", headers=h).status_code == 403
        assert client.get("/api/opportunities/runs/latest", headers=h).status_code == 403
        assert client.get("/api/opportunities/runs/1/status", headers=h).status_code == 403
        assert client.get("/api/opportunities/runs/1/cells/1_1", headers=h).status_code == 403
    assert client.get("/api/opportunities/runs/99999999/status", headers=MGR).status_code == 404

    busy = OpportunityRun(status="running", created_by="bd_manager:asha", progress={"phase": "tiles", "done": 1, "total": 5, "message": "x"})
    db.add(busy)
    db.commit()
    made["runs"].append(busy.id)
    r = client.post("/api/opportunities/runs", headers=MGR)
    assert r.status_code == 202 and r.json() == {"run_id": busy.id, "status": "running", "already_running": True}
    assert client.get(f"/api/opportunities/runs/{busy.id}", headers=MGR).status_code == 409  # not finished yet
    assert len(calls) == 0  # joining a run starts no new work

    busy.created_at = datetime.now(timezone.utc) - timedelta(minutes=C.RUN_STALE_MINUTES + 5)  # a dead run must not block forever
    db.commit()
    assert finder.active_run(db) is None
