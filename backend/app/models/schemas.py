from typing import Literal

from pydantic import BaseModel, Field


class ResolveRequest(BaseModel):
    type: Literal["pincode", "name", "grid_cells"]
    value: str | list[str] = Field(..., description="pincode / locality name, or a list of '<col>_<row>' cell ids")


class GenerateRequest(BaseModel):
    area_id: int
