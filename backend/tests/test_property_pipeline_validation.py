import pytest

from app.services import pipeline as P, property_validation as V

FULL = {"lat": 12.98, "lon": 80.22, "address": "12 Main Rd", "locality": "Velachery", "pincode": "600042",
        "monthly_rent": 90000, "total_area_sqft": 1500, "ground_floor_area_sqft": 1000, "frontage_ft": 20,
        "floor": 0, "number_of_floors": 2, "property_type": "shop", "rent_negotiable": True,
        "is_main_road_frontage": True, "is_corner_property": False, "entry_access": "easy", "exit_access": "easy",
        "two_wheeler_parking": True, "four_wheeler_parking": False}
PHOTOS = {"front_view", "road_view", "interior_view"}


def test_complete_property_passes():
    errors, _ = V.validate_for_submission(FULL, PHOTOS)
    assert errors == []


def test_missing_rent_message_and_field():
    d = {**FULL, "monthly_rent": None}
    errors, _ = V.validate_for_submission(d, PHOTOS)
    assert {"field": "monthly_rent", "message": "Monthly rent is required before submitting this property."} in errors


def test_required_photos():
    errors, _ = V.validate_for_submission(FULL, {"front_view"})
    fields = {e["field"] for e in errors}
    assert {"photo:road_view", "photo:interior_view"} <= fields and "photo:front_view" not in fields


def test_false_booleans_are_not_missing():
    d = {**FULL, "is_corner_property": False, "four_wheeler_parking": False, "rent_negotiable": False}
    assert V.validate_for_submission(d, PHOTOS)[0] == []


def test_plausibility_errors_and_warnings():
    e, _ = V.plausibility({**FULL, "ground_floor_area_sqft": 2000})
    assert any(x["field"] == "ground_floor_area_sqft" for x in e)
    e, _ = V.plausibility({**FULL, "sales_area_sqft": 1200, "storage_area_sqft": 600})
    assert any(x["field"] == "sales_area_sqft" for x in e)
    e, _ = V.plausibility({**FULL, "lat": 19.07, "lon": 72.87})  # Mumbai
    assert any(x["field"] == "location" for x in e)
    e, _ = V.plausibility({**FULL, "pincode": "60004"})
    assert any(x["field"] == "pincode" for x in e)
    _, w = V.plausibility({**FULL, "location_accuracy_m": 400})
    assert any(x["field"] == "location" for x in w)
    _, w = V.plausibility({**FULL, "monthly_rent": 5_000_000})
    assert any(x["field"] == "monthly_rent" for x in w)


def test_address_normalisation():
    assert V.normalize_address("No. 12,  Sony Nagar 2nd St.") == V.normalize_address("12 sony nagar 2nd street")
    assert V.normalize_address("   ") is None and V.normalize_address(None) is None


@pytest.mark.parametrize("frm,to,who,ok", [
    ("ASSIGNED", "SUBMITTED", "bd_executive:ravi", True),
    ("ASSIGNED", "UNDER_REVIEW", "bd_manager:asha", False),  # cannot skip submit
    ("SUBMITTED", "UNDER_REVIEW", "bd_executive:ravi", False),  # executives cannot review
    ("SUBMITTED", "UNDER_REVIEW", "bd_manager:asha", True),
    ("UNDER_REVIEW", "REJECTED", "bd_manager:asha", False),  # no reject before the catchment is done
    ("SUBMITTED", "REJECTED", "bd_manager:asha", False),
    ("CATCHMENT_REQUESTED", "REJECTED", "bd_manager:asha", False),
    ("CATCHMENT_COMPLETED", "REJECTED", "bd_manager:asha", False),  # decide only from FINAL_REVIEW
    ("FINAL_REVIEW", "REJECTED", "bd_manager:asha", True),
    ("UNDER_REVIEW", "CATCHMENT_REQUESTED", "bd_manager:asha", True),
    ("UNDER_REVIEW", "APPROVED", "bd_manager:asha", False),  # approval only after final review
    ("REJECTED", "UNDER_REVIEW", "bd_manager:asha", False),  # terminal
    ("CATCHMENT_REQUESTED", "CATCHMENT_IN_PROGRESS", "survey_manager:meena", True),
    ("FINAL_REVIEW", "APPROVED", "bd_manager:asha", True),
])
def test_state_machine(frm, to, who, ok):
    if ok:
        P.check_transition(frm, to, who, "because")
    else:
        with pytest.raises(P.TransitionError):
            P.check_transition(frm, to, who, "because")


def test_reason_required_for_every_decision_including_approve():
    for to, frm in (("ASSIGNED", "UNDER_REVIEW"), ("APPROVED", "FINAL_REVIEW"), ("REJECTED", "FINAL_REVIEW")):
        with pytest.raises(P.TransitionError) as e:
            P.check_transition(frm, to, "bd_manager:asha", "  ")
        assert e.value.status == 422
    P.check_transition("SUBMITTED", "UNDER_REVIEW", "bd_manager:asha", None)  # starting review needs no reason


def test_requesting_a_catchment_study_needs_no_reason():
    P.check_transition("UNDER_REVIEW", "CATCHMENT_REQUESTED", "bd_manager:asha", None)
    P.check_transition("UNDER_REVIEW", "CATCHMENT_REQUESTED", "bd_manager:asha", "   ")
    assert "CATCHMENT_REQUESTED" not in P.REASON_REQUIRED
    assert P.DEFAULT_REASON["CATCHMENT_REQUESTED"] == "Catchment study requested"


def test_allowed_next_by_role():
    assert P.allowed_next("UNDER_REVIEW", "bd_manager") == ["ASSIGNED", "CATCHMENT_REQUESTED"]  # no reject here
    assert P.allowed_next("SUBMITTED", "bd_manager") == ["ASSIGNED", "UNDER_REVIEW"]
    assert P.allowed_next("CATCHMENT_REQUESTED", "bd_manager") == []
    assert P.allowed_next("UNDER_REVIEW", "bd_executive") == []


def test_final_review_path_and_roles():
    # the catchment stages are driven by the survey side / system, never by the BD manager or executive
    for who in ("bd_manager:asha", "bd_executive:ravi"):
        with pytest.raises(P.TransitionError):
            P.check_transition("CATCHMENT_REQUESTED", "CATCHMENT_IN_PROGRESS", who, "x")
    P.check_transition("CATCHMENT_IN_PROGRESS", "CATCHMENT_COMPLETED", "system:m3", None)
    P.check_transition("CATCHMENT_COMPLETED", "FINAL_REVIEW", "bd_manager:asha", None)  # manager starts final review
    P.check_transition("CATCHMENT_COMPLETED", "FINAL_REVIEW", "system:m3", None)  # or M3 does automatically
    # only the BD manager decides, and only from FINAL_REVIEW
    P.check_transition("FINAL_REVIEW", "APPROVED", "bd_manager:asha", "Catchment confirmed strong footfall")
    with pytest.raises(P.TransitionError):
        P.check_transition("FINAL_REVIEW", "APPROVED", "bd_executive:ravi", "x")
    with pytest.raises(P.TransitionError):
        P.check_transition("CATCHMENT_COMPLETED", "APPROVED", "bd_manager:asha", "skipping final review")
    assert P.allowed_next("FINAL_REVIEW", "bd_manager") == ["APPROVED", "REJECTED"]
    assert P.allowed_next("FINAL_REVIEW", "bd_executive") == []
    assert P.allowed_next("APPROVED", "bd_manager") == [] and P.allowed_next("REJECTED", "bd_manager") == []
