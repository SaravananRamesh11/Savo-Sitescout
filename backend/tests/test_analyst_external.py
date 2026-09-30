"""External-data tools of the conversational analyst. Nominatim and Overpass are faked, so only the database is real."""
import pytest

from app.core import config
from app.core.personas import BY_ID
from app.services import analyst, analyst_external as X, analyst_tools as T, demographics, geocoding

from tests.test_analyst import FakeLLM, data  # noqa: E402,F401  (data = fixture with two analysed areas)

pytestmark = pytest.mark.skipif(not config.DB_URL, reason="database url not configured")
PERSONA = BY_ID["bd_manager:asha"]


@pytest.fixture()
def db():
    from app.core.db import get_session

    s = get_session()
    X._count_cache.clear()
    yield s
    s.close()


class Resp:
    def __init__(self, counts=None, status=200):
        self.status_code, self.headers = status, {"content-type": "application/json"}
        self._counts = counts

    def json(self):
        return {"elements": [{"type": "count", "tags": {"total": str(n)}} for n in self._counts]}


ROW = {"name": "Perungudi", "type": "suburb", "category": "place", "lat": "12.9650", "lon": "80.2461",
       "display_name": "Perungudi, Chennai, Tamil Nadu, 600096, India"}


def test_list_chennai_areas(db):
    r = T.run_tool(db, PERSONA, "list_chennai_areas", {})
    assert r["found"] and r["pincodes_total"] >= 100
    assert r["chennai_corporation_zones_total"] >= 10 and r["suburb_areas_total"] >= 5
    assert all(z["name"].lower().startswith("zone") for z in r["chennai_corporation_zones"])
    assert not any(z["name"].lower().startswith("ward") for z in r["suburb_areas"])
    assert r["suburb_areas_not_shown"] == max(0, r["suburb_areas_total"] - len(r["suburb_areas"]))  # computed, not left to the model
    assert "Analyse tab" in r["next_step"] and r["sources"][0]["type"] == "external"
    f = T.run_tool(db, PERSONA, "list_chennai_areas", {"query": "velachery"})
    assert f["found"] and f["pincodes_total"] < r["pincodes_total"]
    assert T.run_tool(db, PERSONA, "list_chennai_areas", {"query": "zzzz-nowhere"})["found"] is False
    p = T.run_tool(db, PERSONA, "list_chennai_areas", {"query": "600042"})  # a pincode query lists the pincodes themselves
    assert p["found"] and any("600042" in x.get("pincodes", []) for x in p["chennai_corporation_zones"] + p["suburb_areas"])


def test_lookup_place(db, monkeypatch):
    monkeypatch.setattr(X, "_search_nominatim", lambda q, limit=3: [ROW])
    r = T.run_tool(db, PERSONA, "lookup_place", {"query": "Perungudi"})
    p = r["places"][0]
    assert r["found"] and p["name"] == "Perungudi" and p["inside_chennai_region"] is True
    assert isinstance(p["saved_areas_containing_it"], list)
    assert r["sources"][0]["href"].startswith("https://www.openstreetmap.org/?mlat=12.96500")
    monkeypatch.setattr(X, "_search_nominatim", lambda q, limit=3: [])
    assert T.run_tool(db, PERSONA, "lookup_place", {"query": "Atlantis"})["found"] is False

    def down(q, limit=3):
        raise geocoding.AreaResolveError("Geocoder unreachable")

    monkeypatch.setattr(X, "_search_nominatim", down)
    r = T.run_tool(db, PERSONA, "lookup_place", {"query": "Perungudi"})
    assert r["found"] is False and r["note"] == "Insufficient data."


def test_place_demographics_is_labelled_an_estimate_and_works_offline(db, monkeypatch):
    def no_network(*a, **k):
        raise AssertionError("a table locality must not call the geocoder")

    monkeypatch.setattr(X, "_search_nominatim", no_network)
    zone = demographics.load_mock()["zones"][0]["name"]
    r = T.run_tool(db, PERSONA, "place_demographics", {"place": zone})
    assert r["found"] and r["place_found_via"] == "bundled locality table"
    assert r["estimate"]["population_per_km2"] > 0
    assert "ESTIMATE" in r["basis"] and "Not official Census" in r["basis"] and "(not Census)" in r["sources"][0]["label"]
    monkeypatch.setattr(X, "_search_nominatim", lambda q, limit=3: [])
    assert T.run_tool(db, PERSONA, "place_demographics", {"place": "Atlantis Zzz"})["found"] is False


def test_nearby_amenities_counts_clamp_cache_and_failure(db, monkeypatch):
    zone = demographics.load_mock()["zones"][0]["name"]
    posts = []

    def post(url, data=None, headers=None, timeout=None):
        posts.append(data["data"])
        return Resp([4, 12, 30, 3, 2, 9, 7, 25])

    monkeypatch.setattr(X.httpx, "post", post)
    r = T.run_tool(db, PERSONA, "nearby_amenities", {"place": zone, "radius_m": 9000})
    assert r["found"] and r["radius_m"] == 2000  # clamped
    assert r["counts_within_radius"] == dict(zip(X.CATEGORIES, [4, 12, 30, 3, 2, 9, 7, 25]))
    assert "minimum" in r["basis"] and r["sources"][0]["type"] == "external"
    assert posts[0].count("out count;") == len(X.CATEGORIES) and "(around:2000," in posts[0]
    T.run_tool(db, PERSONA, "nearby_amenities", {"place": zone, "radius_m": 9000})
    assert len(posts) == 1  # served from the in-memory cache
    one = T.run_tool(db, PERSONA, "nearby_amenities", {"place": zone, "category": "school", "radius_m": 100})
    assert one["counts_within_radius"] == {"school": 30} and one["radius_m"] == 300
    X._count_cache.clear()
    monkeypatch.setattr(X.httpx, "post", lambda *a, **k: Resp([1], status=429))  # wrong shape / rate limited
    r = T.run_tool(db, PERSONA, "nearby_amenities", {"place": zone})
    assert r["found"] is False and r["note"] == "Insufficient data."


def test_external_tool_arguments_are_validated():
    with pytest.raises(ValueError):
        T.validate_args("nearby_amenities", {"place": "Adyar", "category": "casino"})
    with pytest.raises(ValueError):
        T.validate_args("lookup_place", {})
    assert T.validate_args("nearby_amenities", {"place": "Adyar", "radius_m": "800"}) == {"place": "Adyar", "radius_m": 800}


def test_areas_question_end_to_end_is_grounded(db, monkeypatch):
    FakeLLM(monkeypatch, {"calls": [{"tool": "list_chennai_areas", "args": {}}]}, ["temp"])
    total = T.run_tool(db, PERSONA, "list_chennai_areas", {})["chennai_corporation_zones_total"]
    FakeLLM(monkeypatch, {"calls": [{"tool": "list_chennai_areas", "args": {}}]},
            [f"According to the OGD India pincode boundaries, there are {total} Chennai Corporation zones you can scout."])
    out = analyst.answer(db, PERSONA, "What areas are in Chennai that I can scout?")
    assert out["mode"] == "llm" and str(total) in out["answer"]
    assert out["sources"][0]["type"] == "external"
    # a made-up number is rejected and the deterministic template (built from the same data) is used instead
    FakeLLM(monkeypatch, {"calls": [{"tool": "list_chennai_areas", "args": {}}]}, ["There are 4242 areas.", "4242 again."])
    out = analyst.answer(db, PERSONA, "areas?")
    assert out["mode"] == "template" and "4242" not in out["answer"] and str(total) in out["answer"]


def test_compare_with_an_unanalysed_area_is_partial_not_a_failure(data, monkeypatch):
    zone = demographics.load_mock()["zones"][0]["name"]
    monkeypatch.setattr(X, "_search_nominatim", lambda q, limit=3: [])
    r = T.run_tool(data.db, PERSONA, "compare_areas", {"areas": [data.area_a.resolved_name, zone]})
    assert r["found"] and r["partial"] is True and "best_area" not in r  # nothing is ranked
    assert [a["area_name"] for a in r["analysed_areas"]] == [data.area_a.resolved_name]
    assert r["analysed_areas"][0]["overall_score"] == 72.5
    est = r["not_analysed_areas"][0]
    assert est["name"] == zone and est["estimate"]["population_per_km2"] > 0 and "ESTIMATE" in est["basis"]
    assert "Analyse tab" in r["note"] and {s["type"] for s in r["sources"]} == {"area_report", "external"}
    # neither analysed nor locatable: still the honest Insufficient data
    r = T.run_tool(data.db, PERSONA, "compare_areas", {"areas": ["Atlantis Zzz", "Nowhereville Qq"]})
    assert r["found"] is False and r["note"] == "Insufficient data."
    # both analysed: the normal ranked comparison is unchanged
    r = T.run_tool(data.db, PERSONA, "compare_areas", {"areas": [data.area_a.resolved_name, data.area_b.resolved_name]})
    assert r["found"] and "partial" not in r and r["best_area"] == data.area_a.resolved_name
