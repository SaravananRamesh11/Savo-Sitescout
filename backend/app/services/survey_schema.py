"""What a Survey Executive may record, per capture type. `data` stores ONLY what was observed.

Unknown keys are rejected and every choice is a closed list, so the aggregation never has to guess. No exact traffic
counts are collected: traffic is LOW / MEDIUM / HIGH with an observation period, as the spec requires.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

Level = Literal["low", "medium", "high"]
Ease = Literal["easy", "moderate", "difficult"]
Size = Literal["small", "medium", "large"]


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    notes: str | None = Field(default=None, max_length=500)


class Residential(_Base):
    buildings_visible: int | None = Field(default=None, ge=0, le=5000)
    independent_houses: int | None = Field(default=None, ge=0, le=5000)
    apartment_complexes: int | None = Field(default=None, ge=0, le=500)
    occupancy: Literal["low", "medium", "high", "unclear"] | None = None  # only where reasonably observable
    construction_activity: Literal["none", "some", "heavy"] | None = None
    activity_level: Level


class Commercial(_Base):
    business_kind: Literal["grocery", "supermarket", "convenience", "pharmacy", "other"]
    name: str | None = Field(default=None, max_length=120)
    count: int = Field(default=1, ge=1, le=200)
    activity_level: Level


class Competition(_Base):
    name: str | None = Field(default=None, max_length=120)
    competitor_type: Literal["supermarket", "organised_grocery", "convenience", "kirana", "other"]
    size: Size
    customer_activity: Level


class Traffic(_Base):
    pedestrian: Level
    vehicle: Level
    observation_period: Literal["morning", "midday", "evening", "night"]
    observation_minutes: int | None = Field(default=None, ge=1, le=600)
    peak_notes: str | None = Field(default=None, max_length=300)


class Accessibility(_Base):
    road_condition: Literal["good", "fair", "poor"]
    approx_road_width_ft: float | None = Field(default=None, gt=0, le=200)
    entry_exit: Ease
    parking: Literal["easy", "moderate", "difficult", "none"]
    obstruction: bool = False
    construction: bool = False
    road_closure: bool = False
    median_barrier: bool = False
    difficult_turns: bool = False


class DemandGenerator(_Base):
    kind: Literal["school", "college", "hospital", "apartment_complex", "office_cluster", "market", "transit", "other"]
    name: str | None = Field(default=None, max_length=120)
    size: Size | None = None


class LocalCondition(_Base):
    condition: Literal["construction", "vacant_land", "waterlogging", "blocked_road", "parking_restriction",
                       "temporary_barrier", "other"]
    severity: Level


MODELS = {
    "residential": Residential, "commercial": Commercial, "competition": Competition, "traffic": Traffic,
    "accessibility": Accessibility, "demand_generator": DemandGenerator, "local_condition": LocalCondition,
}
LABELS = {
    "residential": "Residential", "commercial": "Commercial", "competition": "Competition",
    "traffic": "Traffic and footfall", "accessibility": "Accessibility", "demand_generator": "Demand generator",
    "local_condition": "Local condition",
}


def validate_capture(capture_type: str, data: dict) -> dict:
    """Returns the cleaned data dict or raises ValueError with a readable, field-level message."""
    model = MODELS.get(capture_type)
    if model is None:
        raise ValueError(f"Unknown capture type '{capture_type}'")
    try:
        return model(**(data or {})).model_dump(exclude_none=True)
    except ValidationError as exc:
        parts = []
        for e in exc.errors():
            field = ".".join(str(x) for x in e["loc"]) or "data"
            parts.append(f"{field}: {e['msg']}")
        raise ValueError("; ".join(parts)) from None


def form_spec() -> dict:
    """JSON schema per type so the phone form can be built from the server's truth."""
    return {k: m.model_json_schema() for k, m in MODELS.items()}
