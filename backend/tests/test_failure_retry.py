"""Integration test (needs the configured database): a node failure is reported with its name and a retry completes.

Skipped automatically when no database URL is configured.
"""
import uuid

import pytest
from geoalchemy2.shape import from_shape
from shapely.geometry import box

from app.core.db import get_session
from app.models.db_models import Area, AreaReport, ExternalDataCache, GridCell

pytestmark = pytest.mark.skipif(get_session is None, reason="no db")


def _db_or_skip():
    try:
        return get_session()
    except RuntimeError:
        pytest.skip("database url not configured")


def test_failed_node_is_recorded_and_retry_succeeds(monkeypatch):
    from app.agent import graph
    from app.agent.nodes import pipeline
    from app.services import overpass_client

    db = _db_or_skip()
    poly = box(80.2200, 12.9800, 80.2245, 12.9845)  # ~0.25 km2 near Velachery
    area = Area(input_type="grid_cells", raw_input=f"test_retry_{uuid.uuid4().hex[:8]}", resolved_name="Retry test", area_km2=0.25,
                geometry=from_shape(poly, srid=4326), boundary_quality="exact")
    db.add(area)
    db.commit()
    rep = AreaReport(area_id=area.id, status="queued", steps=graph.initial_steps())
    db.add(rep)
    db.commit()

    calls = {"n": 0}
    real = pipeline.calculate_score

    def flaky(state, db_):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return real(state, db_)

    monkeypatch.setattr(pipeline, "NODES", [(n, label, flaky if n == "calculate_score" else fn)
                                            for n, label, fn in pipeline.NODES])
    monkeypatch.setattr(graph, "NODES", pipeline.NODES)
    # keep the test offline and fast: mock OSM path
    monkeypatch.setattr(overpass_client, "fetch_bbox", lambda *a: (_ for _ in ()).throw(RuntimeError("offline")))

    try:
        graph.run_report(rep.id)
        db.expire_all()
        rep = db.get(AreaReport, rep.id)
        assert rep.status == "failed" and rep.failed_node == "calculate_score" and "boom" in rep.error
        assert [s["status"] for s in rep.steps].count("done") == 5
        assert rep.steps[5]["status"] == "failed"

        graph.run_report(rep.id)  # retry: external data now served from cache
        db.expire_all()
        rep = db.get(AreaReport, rep.id)
        assert rep.status == "completed" and rep.overall_score is not None
        assert "osm_mocked" in rep.data_quality_flags  # fell back rather than crashing
    finally:
        db.expire_all()
        db.query(GridCell).filter(GridCell.area_id == area.id).delete()
        db.query(ExternalDataCache).filter(ExternalDataCache.area_id == area.id).delete()
        db.query(AreaReport).filter(AreaReport.area_id == area.id).delete()
        db.query(Area).filter(Area.id == area.id).delete()
        db.commit()
