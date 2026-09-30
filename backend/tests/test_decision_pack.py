"""Decision Pack: PDF content with complete and missing data (no database needed) and the endpoint's access rules."""
import io
from types import SimpleNamespace

import pytest
from pypdf import PdfReader

from app.core import config
from app.services import decision_pack as dp, savomart_client

from tests.test_property_integration import EXEC, MGR, _draft, env  # noqa: F401  (env = the shared property fixture)

SECTIONS = ["1. Property overview", "2. Area intelligence", "3. Property evaluation", "4. Ground catchment survey",
            "5. Final decision", "6. Data sources and dates"]
DECISION = {"decision": "APPROVED", "decided_by_name": "Asha (BD Manager)", "decided_at": "2026-09-29T18:00:09+00:00",
            "reason": "Strong catchment and acceptable rent", "based_on_evaluation": {"version": 2, "overall_score": 71.5}}


def _text(pdf: bytes) -> str:
    assert pdf.startswith(b"%PDF")
    return "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages)


@pytest.fixture()
def offline(monkeypatch):
    """No database, stores API or photos: everything the pack reads outside the property detail is stubbed away."""
    monkeypatch.setattr(dp, "latest_completed_report", lambda db, area_id: None)
    monkeypatch.setattr(savomart_client, "get_stores", lambda db: ([{"name": "Test store", "latitude": 13.0, "longitude": 80.21}], {}))
    return SimpleNamespace(get=lambda *a, **k: None), SimpleNamespace(photos=[], area_id=1)


def test_complete_case_contains_every_section_and_the_decision(offline):
    db, prop = offline
    d = {"property_id": 7, "address": "12 Test Road", "locality": "Velachery", "pincode": "600042", "area_name": "Velachery", "lat": 13.0,
         "lon": 80.2, "property_type": "shop", "total_area_sqft": 1800, "monthly_rent": 145000, "rent_per_sqft": 80.6,
         "final_decision": DECISION, "history": [{"changed_at": "2026-09-29T10:00:00+00:00", "from_stage": "FINAL_REVIEW", "to_stage": "APPROVED",
                                                  "changed_by_name": "Asha", "reason": "ok"}],
         "evaluation": {"version": 2, "overall_score": 71.5, "confidence": 0.75, "recommendation": "PROCEED_TO_CATCHMENT",
                        "created_at": "2026-09-29T09:00:00+00:00", "m1_context": {"area_score": 60.0},
                        "score_breakdown": [{"label": "Parking", "scored": True, "points": 8.0, "effective_weight": 10.0, "explanation": "Two-wheeler: yes"}],
                        "risks": [{"severity": "low", "text": "Rent per sq ft is above the local median"}],
                        "insights": [{"text": "Good frontage on a main road"}], "metrics": {}},
         "catchment": {"status": "COMPLETED", "requested_at": "2026-09-29T09:30:00+00:00", "completed_at": "2026-09-29T10:00:00+00:00",
                       "insights": {"coverage_percentage": 82.0, "ground_fit_score": 74.0, "version": 1,
                                    "residential": {"observations": 4, "sufficient": True, "independent_houses": 30},
                                    "commercial": {"observations": 3, "sufficient": True, "businesses_total": 9},
                                    "key_findings": ["Two schools and a market inside the catchment"],
                                    "risks": [{"severity": "low", "text": "Road works near the entrance"}]},
                       "evidence_photos": []}}
    text = _text(dp.build_pack(db, prop, d))
    for section in SECTIONS:
        assert section in text
    for expected in ["APPROVED", "Asha (BD Manager)", "Strong catchment and acceptable rent", "82 %", "Two schools and a market",
                     "Road works near the entrance", "Rent per sq ft is above the local median", "Rs 145,000", "Generated"]:
        assert expected in text, expected


def test_missing_optional_data_is_reported_not_invented_and_never_breaks_the_pack(offline):
    db, prop = offline
    d = {"property_id": 8, "lat": 13.0, "lon": 80.2, "final_decision": DECISION}  # no address, evaluation, survey, photos or area report
    text = _text(dp.build_pack(db, prop, d))
    for section in SECTIONS:
        assert section in text
    assert "No completed evaluation is available" in text and "No catchment survey was recorded" in text
    assert "No completed area report is available" in text and "Not available" in text and "No photos available" in text


@pytest.mark.skipif(not config.DB_URL, reason="database url not configured")
def test_endpoint_is_manager_only_and_approved_only(env):  # noqa: F811
    client, db, area = env
    pid = _draft(client, area)["property_id"]
    assert client.get(f"/api/properties/{pid}/decision-pack", headers=EXEC).status_code == 403
    r = client.get(f"/api/properties/{pid}/decision-pack", headers=MGR)
    assert r.status_code == 409 and "approved" in r.json()["detail"]
    assert client.get("/api/properties/999999999/decision-pack", headers=MGR).status_code == 404
