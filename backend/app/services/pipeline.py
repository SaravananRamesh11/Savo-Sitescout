"""Property pipeline state machine: the single source of truth for who may move a property where.

ASSIGNED = the editable in-field state (the executive's draft, or a property the manager sent back for changes).
The BD manager cannot reject before the catchment study is done: REJECTED is only reachable from FINAL_REVIEW.
Stages after CATCHMENT_REQUESTED are driven by the survey side (M3) or the system.
"""
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.property_models import Property, PropertyStatusHistory

# from_stage -> {to_stage: roles allowed}
TRANSITIONS: dict[str, dict[str, set[str]]] = {
    "ASSIGNED": {"SUBMITTED": {"bd_executive", "bd_manager"}},
    "SUBMITTED": {"UNDER_REVIEW": {"bd_manager"}, "ASSIGNED": {"bd_manager"}},
    "UNDER_REVIEW": {"CATCHMENT_REQUESTED": {"bd_manager"}, "ASSIGNED": {"bd_manager"}},
    "CATCHMENT_REQUESTED": {"CATCHMENT_IN_PROGRESS": {"survey_manager", "system"}},
    "CATCHMENT_IN_PROGRESS": {"CATCHMENT_COMPLETED": {"survey_manager", "system"}},
    "CATCHMENT_COMPLETED": {"FINAL_REVIEW": {"bd_manager", "system"}},
    "FINAL_REVIEW": {"APPROVED": {"bd_manager"}, "REJECTED": {"bd_manager"}},
    "APPROVED": {},
    "REJECTED": {},
}
ROLE_LABELS = {"bd_manager": "BD Manager", "bd_executive": "BD Executive", "survey_manager": "Survey Manager",
               "survey_executive": "Survey Executive", "system": "System"}
# Final decisions and send-back need a written why (audit). Requesting a catchment study does not: the survey
# executive's capture forms already define what is recorded, so the manager just clicks the button.
REASON_REQUIRED = {"REJECTED", "APPROVED", "ASSIGNED"}
DEFAULT_REASON = {"CATCHMENT_REQUESTED": "Catchment study requested"}


class TransitionError(ValueError):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def role_of(persona_id: str) -> str:
    return persona_id.split(":", 1)[0]


def allowed_next(stage: str, role: str) -> list[str]:
    return sorted(to for to, roles in TRANSITIONS.get(stage, {}).items() if role in roles)


def check_transition(from_stage: str, to_stage: str, persona: str, reason: str | None) -> None:
    role = role_of(persona)
    allowed = TRANSITIONS.get(from_stage, {})
    if to_stage not in allowed:
        raise TransitionError(f"A property in {from_stage} cannot move to {to_stage}")
    if role not in allowed[to_stage]:
        raise TransitionError(f"A {ROLE_LABELS.get(role, role)} cannot move a property from {from_stage} to "
                              f"{to_stage}.", status=403)
    if to_stage in REASON_REQUIRED and not (reason or "").strip():
        raise TransitionError("A reason is required for this change", status=422)


def apply_transition(db: Session, prop: Property, to_stage: str, persona: str, reason: str | None = None,
                     notes: str | None = None, *, check: bool = True,
                     evaluation_id: int | None = None) -> PropertyStatusHistory:
    """Change the stage and write the history row in the same transaction (caller commits)."""
    if check:
        check_transition(prop.pipeline_stage, to_stage, persona, reason)
    row = PropertyStatusHistory(property_id=prop.id, from_stage=prop.pipeline_stage, to_stage=to_stage,
                                changed_by=persona, changed_at=datetime.now(timezone.utc),
                                reason=(reason or None), notes=(notes or None), evaluation_id=evaluation_id)
    prop.pipeline_stage = to_stage
    db.add(row)
    return row
