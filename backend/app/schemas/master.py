from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models import BaselineScope, ChartType, ItemCategory


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---- ItemType
class ItemTypeIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    category: ItemCategory
    description: str | None = None
    is_active: bool = True


class ItemTypeOut(ItemTypeIn, ORM):
    id: int


# ---- ProcessStep
class ProcessStepIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    seq: int = 0
    description: str | None = None
    is_active: bool = True


class ProcessStepOut(ProcessStepIn, ORM):
    id: int


# ---- Equipment
class EquipmentIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    process_step_id: int | None = None
    description: str | None = None
    pos_x: float | None = None
    pos_y: float | None = None
    pos_z: float | None = None
    model_path: str | None = None
    is_active: bool = True


class EquipmentOut(EquipmentIn, ORM):
    id: int


# ---- ConditionDefinition
class ConditionDefinitionIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    unit: str | None = None
    min_value: float | None = None
    max_value: float | None = None
    process_step_id: int | None = None
    value_kind: str | None = Field(default=None, pattern="^(measured|setpoint)$")
    is_active: bool = True

    @model_validator(mode="after")
    def _range(self):
        if self.min_value is not None and self.max_value is not None and self.min_value > self.max_value:
            raise ValueError("허용 범위의 최소값이 최대값보다 큽니다.")
        return self


class ConditionDefinitionOut(ConditionDefinitionIn, ORM):
    id: int
    learned_min: float | None = None
    learned_max: float | None = None
    learned_at: datetime | None = None
    learned_note: str | None = None


# ---- Characteristic
class RuleSetting(BaseModel):
    enabled: bool = True
    params: dict[str, float] = Field(default_factory=dict)


class CharacteristicIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    unit: str | None = None
    target: float | None = None
    usl: float | None = None
    lsl: float | None = None
    subgroup_size: int = Field(default=1, ge=1)
    chart_type: ChartType = ChartType.AUTO
    baseline_scope: BaselineScope = BaselineScope.GLOBAL
    rule_config: dict[str, RuleSetting] | None = None
    item_type_id: int | None = None
    process_step_id: int | None = None
    is_active: bool = True

    @model_validator(mode="after")
    def _check(self):
        if (self.item_type_id is None) == (self.process_step_id is None):
            raise ValueError("특성은 부품 종류(ItemType) 또는 공정 단계 중 하나에만 연결해야 합니다.")
        if self.usl is not None and self.lsl is not None and self.lsl >= self.usl:
            raise ValueError("규격 하한(LSL)이 상한(USL)보다 작아야 합니다.")
        if self.target is not None:
            if (self.usl is not None and self.target > self.usl) or (
                self.lsl is not None and self.target < self.lsl
            ):
                raise ValueError("Target 이 규격 범위 밖에 있습니다.")
        if self.rule_config:
            bad = [k for k in self.rule_config if k not in {str(i) for i in range(1, 9)}]
            if bad:
                raise ValueError(f"넬슨룰 번호는 1~8 이어야 합니다: {', '.join(bad)}")
        return self


class CharacteristicOut(ORM):
    id: int
    name: str
    unit: str | None
    target: float | None
    usl: float | None
    lsl: float | None
    subgroup_size: int
    chart_type: ChartType
    baseline_scope: BaselineScope
    rule_config: dict[str, Any] | None
    item_type_id: int | None
    process_step_id: int | None
    is_active: bool


# ---- ProductDesign
class DesignComponentIn(BaseModel):
    item_type_id: int
    ratio: float | None = Field(default=None, ge=0, le=1)
    qty: float | None = Field(default=None, ge=0)
    tolerance: float | None = Field(default=None, ge=0)


class DesignComponentOut(DesignComponentIn, ORM):
    id: int


class DesignStepIn(BaseModel):
    process_step_id: int
    seq: int


class DesignStepOut(DesignStepIn, ORM):
    id: int


class ProductDesignIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    output_item_type_id: int
    version: int = Field(default=1, ge=1)
    description: str | None = None
    is_active: bool = True
    components: list[DesignComponentIn] = Field(default_factory=list)
    steps: list[DesignStepIn] = Field(default_factory=list)


class ProductDesignOut(ORM):
    id: int
    name: str
    output_item_type_id: int
    version: int
    description: str | None
    is_active: bool
    components: list[DesignComponentOut]
    steps: list[DesignStepOut]


# ---- DefectType
class DefectTypeIn(BaseModel):
    code: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    is_active: bool = True


class DefectTypeOut(DefectTypeIn, ORM):
    id: int
