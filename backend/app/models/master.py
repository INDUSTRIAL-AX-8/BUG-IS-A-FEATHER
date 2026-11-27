"""마스터 데이터 — 실무자/관리자가 화면에서 관리한다. 특정 부품·공정명을 코드에 두지 않는다."""

from __future__ import annotations

from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.enums import BaselineScope, ChartType, ItemCategory


def _enum(e):
    return Enum(e, native_enum=False, length=20, validate_strings=True)


class ItemType(Base):
    __tablename__ = "item_type"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    category: Mapped[ItemCategory] = mapped_column(_enum(ItemCategory))
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class ProcessStep(Base):
    __tablename__ = "process_step"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Equipment(Base):
    __tablename__ = "equipment"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    process_step_id: Mapped[int | None] = mapped_column(ForeignKey("process_step.id"))
    description: Mapped[str | None] = mapped_column(Text)
    # 3D 확장용 자리 (지금은 사용하지 않음)
    pos_x: Mapped[float | None] = mapped_column(Float)
    pos_y: Mapped[float | None] = mapped_column(Float)
    pos_z: Mapped[float | None] = mapped_column(Float)
    model_path: Mapped[str | None] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    process_step: Mapped[ProcessStep | None] = relationship()


class ConditionDefinition(Base):
    __tablename__ = "condition_definition"
    __table_args__ = (UniqueConstraint("name", "process_step_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    unit: Mapped[str | None] = mapped_column(String(30))
    min_value: Mapped[float | None] = mapped_column(Float)
    max_value: Mapped[float | None] = mapped_column(Float)
    process_step_id: Mapped[int | None] = mapped_column(ForeignKey("process_step.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Characteristic(Base):
    """ItemType 또는 ProcessStep 중 정확히 하나에 붙는 측정 특성."""

    __tablename__ = "characteristic"
    __table_args__ = (
        CheckConstraint(
            "(item_type_id IS NULL) <> (process_step_id IS NULL)", name="owner_xor"
        ),
        CheckConstraint("subgroup_size >= 1", name="subgroup_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    unit: Mapped[str | None] = mapped_column(String(30))
    target: Mapped[float | None] = mapped_column(Float)
    usl: Mapped[float | None] = mapped_column(Float)
    lsl: Mapped[float | None] = mapped_column(Float)
    subgroup_size: Mapped[int] = mapped_column(Integer, default=1)
    chart_type: Mapped[ChartType] = mapped_column(_enum(ChartType), default=ChartType.AUTO)
    baseline_scope: Mapped[BaselineScope] = mapped_column(
        _enum(BaselineScope), default=BaselineScope.GLOBAL, server_default=BaselineScope.GLOBAL.value
    )
    # {"1": {"enabled": true, "params": {...}}, ...}; null 이면 전역 기본값
    rule_config: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    item_type_id: Mapped[int | None] = mapped_column(ForeignKey("item_type.id"), index=True)
    process_step_id: Mapped[int | None] = mapped_column(ForeignKey("process_step.id"), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    item_type: Mapped[ItemType | None] = relationship()
    process_step: Mapped[ProcessStep | None] = relationship()


class ProductDesign(Base):
    """출력 ItemType 이 어떤 ItemType 을 어떤 비율로 쓰고 어떤 공정 순서를 거치는지의 기대 구조."""

    __tablename__ = "product_design"
    __table_args__ = (UniqueConstraint("output_item_type_id", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    output_item_type_id: Mapped[int] = mapped_column(ForeignKey("item_type.id"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    components: Mapped[list[ProductDesignComponent]] = relationship(
        cascade="all, delete-orphan", order_by="ProductDesignComponent.id"
    )
    steps: Mapped[list[ProductDesignStep]] = relationship(
        cascade="all, delete-orphan", order_by="ProductDesignStep.seq"
    )


class ProductDesignComponent(Base):
    __tablename__ = "product_design_component"

    id: Mapped[int] = mapped_column(primary_key=True)
    design_id: Mapped[int] = mapped_column(
        ForeignKey("product_design.id", ondelete="CASCADE"), index=True
    )
    item_type_id: Mapped[int] = mapped_column(ForeignKey("item_type.id"))
    ratio: Mapped[float | None] = mapped_column(Float)  # 투입 비율 (0~1)
    qty: Mapped[float | None] = mapped_column(Float)  # 단위당 투입 수량
    tolerance: Mapped[float | None] = mapped_column(Float)  # 비율 허용 오차 (절대값)


class ProductDesignStep(Base):
    __tablename__ = "product_design_step"

    id: Mapped[int] = mapped_column(primary_key=True)
    design_id: Mapped[int] = mapped_column(
        ForeignKey("product_design.id", ondelete="CASCADE"), index=True
    )
    process_step_id: Mapped[int] = mapped_column(ForeignKey("process_step.id"))
    seq: Mapped[int] = mapped_column(Integer)


class DefectType(Base):
    __tablename__ = "defect_type"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
