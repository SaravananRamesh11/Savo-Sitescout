"""Seeded demo personas and the tiny access-control layer (no real auth, per the brief: a role switcher is enough).

The frontend sends the chosen persona id in the `X-Persona` header; the API checks it against this list and uses the
role for permission checks and the id for `created_by` / `changed_by` fields.
"""
from fastapi import Header, HTTPException

PERSONAS = [
    {"id": "bd_manager:asha", "name": "Asha (BD Manager)", "role": "bd_manager", "role_label": "BD Manager",
     "active": True, "job": "Explore areas, direct scouting, review properties, request catchment studies"},
    {"id": "bd_executive:ravi", "name": "Ravi (BD Executive)", "role": "bd_executive",
     "role_label": "BD Executive", "active": True, "job": "Scout properties in the field from a phone"},
    {"id": "bd_executive:divya", "name": "Divya (BD Executive)", "role": "bd_executive",
     "role_label": "BD Executive", "active": True, "job": "Scout properties in the field from a phone"},
    {"id": "survey_manager:meena", "name": "Meena (Survey Manager)", "role": "survey_manager",
     "role_label": "Survey Manager", "active": False, "job": "Plan and assign catchment studies (Milestone 3)"},
    {"id": "survey_executive:karthik", "name": "Karthik (Survey Executive)", "role": "survey_executive",
     "role_label": "Survey Executive", "active": False, "job": "Capture lane-level data (Milestone 3)"},
]
BY_ID = {p["id"]: p for p in PERSONAS}
EXECUTIVES = [p for p in PERSONAS if p["role"] == "bd_executive"]


def current_persona(x_persona: str | None = Header(default=None)) -> dict:
    """Dependency: resolve the X-Persona header. Defaults to the BD Manager so plain API calls keep working."""
    pid = x_persona or "bd_manager:asha"
    if pid not in BY_ID:
        raise HTTPException(status_code=401, detail="Unknown persona. Pick a role from the switcher.")
    return BY_ID[pid]


def require_roles(persona: dict, *roles: str) -> None:
    if persona["role"] not in roles:
        raise HTTPException(status_code=403, detail=f"The {persona['role_label']} role cannot do this.")
