from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.models import LotStatus
from app.schemas.master import ORM


class LotIn(BaseModel):
    lot_no: str = Field(min_length=1, max_length=100)
    item_type_id: int
    created_at: datetime
    qty: float | None = None
    status: LotStatus = LotStatus.NORMAL


class LotOut(ORM):
    id: int
    lot_no: str
    item_type_id: int
    item_type_name: str | None = None
    created_at: datetime
    qty: float | None
    status: LotStatus


class LotDetail(LotOut):
    parent_count: int
    child_count: int
    judgment_count: int


class EdgeIn(BaseModel):
    parent_lot_no: str
    child_lot_no: str
    input_qty: float | None = Field(default=None, ge=0)
    input_ratio: float | None = Field(default=None, ge=0, le=1)


class EdgeOut(ORM):
    id: int
    parent_lot_id: int
    child_lot_id: int
    input_qty: float | None
    input_ratio: float | None


class ConditionValueOut(BaseModel):
    condition_definition_id: int
    name: str
    unit: str | None
    value: float
    min_value: float | None
    max_value: float | None
    out_of_range: bool


class ProcessRunOut(BaseModel):
    id: int
    lot_id: int
    process_step_id: int
    process_step_name: str
    equipment_id: int | None
    equipment_name: str | None
    started_at: datetime
    ended_at: datetime | None
    operator_id: str | None
    conditions: list[ConditionValueOut]


class MeasurementOut(ORM):
    id: int
    characteristic_id: int
    lot_id: int | None
    process_run_id: int | None
    measured_at: datetime
    subgroup_no: int | None
    value: float


class GenealogyGraph(BaseModel):
    root_lot_id: int
    direction: str
    depth: int
    truncated: bool
    lots: list[LotOut]
    edges: list[EdgeOut]
