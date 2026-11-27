"""실물 LOT, 계보(DAG), 공정 처리, 조건값, 측정값, SPC/예측 결과."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.enums import ChartType, ForecastStatus, LotStatus
from app.models.master import (
    Characteristic,
    ConditionDefinition,
    Equipment,
    ItemType,
    ProcessStep,
)


def _enum(e):
    return Enum(e, native_enum=False, length=20, validate_strings=True)


class Lot(Base):
    __tablename__ = "lot"

    id: Mapped[int] = mapped_column(primary_key=True)
    lot_no: Mapped[str] = mapped_column(String(100), unique=True)
    item_type_id: Mapped[int] = mapped_column(ForeignKey("item_type.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    qty: Mapped[float | None] = mapped_column(Float)
    status: Mapped[LotStatus] = mapped_column(_enum(LotStatus), default=LotStatus.NORMAL)

    item_type: Mapped[ItemType] = relationship()


class GenealogyEdge(Base):
    """parent_lot 이 child_lot 에 투입됨. 병합(여러 parent)과 분할(여러 child) 모두 표현."""

    __tablename__ = "genealogy_edge"
    __table_args__ = (
        UniqueConstraint("parent_lot_id", "child_lot_id"),
        CheckConstraint("parent_lot_id <> child_lot_id", name="no_self_loop"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    parent_lot_id: Mapped[int] = mapped_column(ForeignKey("lot.id"), index=True)
    child_lot_id: Mapped[int] = mapped_column(ForeignKey("lot.id"), index=True)
    input_qty: Mapped[float | None] = mapped_column(Float)
    input_ratio: Mapped[float | None] = mapped_column(Float)


class ProcessRun(Base):
    __tablename__ = "process_run"

    id: Mapped[int] = mapped_column(primary_key=True)
    lot_id: Mapped[int] = mapped_column(ForeignKey("lot.id"), index=True)
    process_step_id: Mapped[int] = mapped_column(ForeignKey("process_step.id"), index=True)
    equipment_id: Mapped[int | None] = mapped_column(ForeignKey("equipment.id"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime)
    operator_id: Mapped[str | None] = mapped_column(String(100))

    process_step: Mapped[ProcessStep] = relationship()
    equipment: Mapped[Equipment | None] = relationship()
    conditions: Mapped[list[ConditionValue]] = relationship(back_populates="run")


class ConditionValue(Base):
    __tablename__ = "condition_value"
    __table_args__ = (UniqueConstraint("process_run_id", "condition_definition_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    process_run_id: Mapped[int] = mapped_column(ForeignKey("process_run.id"), index=True)
    condition_definition_id: Mapped[int] = mapped_column(
        ForeignKey("condition_definition.id"), index=True
    )
    value: Mapped[float] = mapped_column(Float)

    run: Mapped[ProcessRun] = relationship(back_populates="conditions")
    definition: Mapped[ConditionDefinition] = relationship()


class Measurement(Base):
    __tablename__ = "measurement"
    __table_args__ = (
        CheckConstraint(
            "lot_id IS NOT NULL OR process_run_id IS NOT NULL", name="has_target"
        ),
        Index("ix_measurement_char_time", "characteristic_id", "measured_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    characteristic_id: Mapped[int] = mapped_column(ForeignKey("characteristic.id"))
    lot_id: Mapped[int | None] = mapped_column(ForeignKey("lot.id"), index=True)
    process_run_id: Mapped[int | None] = mapped_column(ForeignKey("process_run.id"), index=True)
    measured_at: Mapped[datetime] = mapped_column(DateTime)
    subgroup_no: Mapped[int | None] = mapped_column(Integer)
    value: Mapped[float] = mapped_column(Float)

    characteristic: Mapped[Characteristic] = relationship()


class ControlLimit(Base):
    """기준 구간으로 계산한 관리한계. 버전을 쌓고 is_locked 인 최신 버전으로 판정한다."""

    __tablename__ = "control_limit"
    __table_args__ = (UniqueConstraint("characteristic_id", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    characteristic_id: Mapped[int] = mapped_column(ForeignKey("characteristic.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    chart_type: Mapped[ChartType] = mapped_column(_enum(ChartType))
    baseline_from: Mapped[datetime | None] = mapped_column(DateTime)
    baseline_to: Mapped[datetime | None] = mapped_column(DateTime)
    center: Mapped[float] = mapped_column(Float)
    ucl: Mapped[float] = mapped_column(Float)
    lcl: Mapped[float] = mapped_column(Float)
    sigma: Mapped[float] = mapped_column(Float)
    dispersion_center: Mapped[float | None] = mapped_column(Float)  # MR/R/S 관리도
    dispersion_ucl: Mapped[float | None] = mapped_column(Float)
    dispersion_lcl: Mapped[float | None] = mapped_column(Float)
    n_subgroups: Mapped[int] = mapped_column(Integer)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("app_user.id"))


class SpcViolation(Base):
    __tablename__ = "spc_violation"
    __table_args__ = (
        UniqueConstraint("characteristic_id", "rule_no", "occurred_at"),
        Index("ix_spc_violation_char_time", "characteristic_id", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    characteristic_id: Mapped[int] = mapped_column(ForeignKey("characteristic.id"))
    control_limit_id: Mapped[int | None] = mapped_column(ForeignKey("control_limit.id"))
    rule_no: Mapped[int] = mapped_column(Integer)
    # 위반에 포함된 포인트들: [{"subgroup": n, "measured_at": iso, "lot_ids": [...]}]
    points: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    lot_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    occurred_at: Mapped[datetime] = mapped_column(DateTime)  # 마지막 포인트 시각
    detected_at: Mapped[datetime] = mapped_column(DateTime)
    acknowledged_by: Mapped[int | None] = mapped_column(ForeignKey("app_user.id"))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime)


class ForecastResult(Base):
    __tablename__ = "forecast_result"

    id: Mapped[int] = mapped_column(primary_key=True)
    characteristic_id: Mapped[int] = mapped_column(ForeignKey("characteristic.id"), index=True)
    run_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    status: Mapped[ForecastStatus] = mapped_column(_enum(ForecastStatus))
    message: Mapped[str | None] = mapped_column(String(500))
    order_p: Mapped[int | None] = mapped_column(Integer)
    order_d: Mapped[int | None] = mapped_column(Integer)
    order_q: Mapped[int | None] = mapped_column(Integer)
    aic: Mapped[float | None] = mapped_column(Float)
    # {"index": [...], "mean": [...], "lower": [...], "upper": [...]}
    forecast: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    risk_score: Mapped[float | None] = mapped_column(Float)
    expected_breach_step: Mapped[int | None] = mapped_column(Integer)
    expected_breach_at: Mapped[datetime | None] = mapped_column(DateTime)
    trend: Mapped[str | None] = mapped_column(String(10))  # up / down / flat


class EquipmentEvent(Base):
    """설비 정지·정비·금형 교체 등 이벤트. 요인 분석의 전후 비교와 타임라인에 쓰인다."""

    __tablename__ = "equipment_event"
    __table_args__ = (Index("ix_equipment_event_time", "equipment_id", "started_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    equipment_id: Mapped[int] = mapped_column(ForeignKey("equipment.id"))
    kind: Mapped[str] = mapped_column(String(30))  # STOP / MAINTENANCE / MOLD_CHANGE / ...
    started_at: Mapped[datetime] = mapped_column(DateTime)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime)
    note: Mapped[str | None] = mapped_column(String(500))
