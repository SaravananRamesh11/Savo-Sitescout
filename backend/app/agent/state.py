from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    report_id: int
    area_id: int
    area_name: str
    input_type: str
    boundary_quality: str
    polygon_wkt: str
    area_km2: float
    cells: list  # [(col,row,coverage)]
    osm: dict
    demographics: dict  # {cell_id: {...}}
    stores: list
    cell_features: dict
    area_features: dict
    area_totals: dict
    profile: dict
    cell_scores: dict
    area_score: dict
    hotspot_ids: list
    hotspots: list
    facts: dict
    explanation: dict
    explanation_source: str
    flags: list
    sources: list
    notes: dict[str, Any]
