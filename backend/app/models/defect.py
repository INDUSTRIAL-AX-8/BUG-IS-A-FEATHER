"""불량 판정, 원인 확정, 판정 시점 후보 스냅샷, 감사 로그."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Enum, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.enums import NodeType
from app.models.master import DefectType
from app.models.transaction import Lot


class DefectJudgment(Base):
    __tablename__ = "defect_judgment"

    id: Mapped[int] = mapped_column(primary_key=True)
    lot_id: Mapped[int] = mapped_column(ForeignKey("lot.id"), index=True)
    defect_type_id: Mapped[int] = mapped_column(ForeignKey("defect_type.id"), index=True)
    judged_by: Mapped[int | None] = mapped_column(ForeignKey("app_user.id"))
    judged_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    memo: Mapped[str | None] = mapped_column(Text)
    # 집계 기록(예: 1시간 300개 중 33개)처럼 LOT 안 일부만 불량일 때의 불량 수량. null 이면 LOT 전체 불량
    defect_qty: Mapped[float | None] = mapped_column(Float)
    attachments: Mapped[list[str]] = mapped_column(JSON, default=list)
    is_cancelled: Mapped[bool] = mapped_column(Boolean, default=False)
    cancelled_by: Mapped[int | None] = mapped_column(ForeignKey("app_user.id"))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime)

    lot: Mapped[Lot] = relationship()
    defect_type: Mapped[DefectType] = relationship()


class DefectSuspect(Base):
    """불량 의심 — 실시간 수집·검사에서 자동으로 걸린 것. 실무자가 확정해야 불량 판정(DefectJudgment)이 된다.

    stage  : 의심이 나온 공정 단계 (예: 사출, 외관 검사)
    source : CONDITION(조건 이탈) / SPC(관리도 위반) / INSPECTION(검사 불합격) / MANUAL
    """

    __tablename__ = "defect_suspect"
    __table_args__ = (Index("ix_defect_suspect_status_stage", "status", "process_step_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    lot_id: Mapped[int] = mapped_column(ForeignKey("lot.id"), index=True)
    process_step_id: Mapped[int | None] = mapped_column(ForeignKey("process_step.id"))
    equipment_id: Mapped[int | None] = mapped_column(ForeignKey("equipment.id"))
    source: Mapped[str] = mapped_column(String(20))
    suspected_qty: Mapped[float | None] = mapped_column(Float)  # null = LOT 전체 검토 필요
    defect_type_id: Mapped[int | None] = mapped_column(ForeignKey("defect_type.id"))  # 추정 유형
    detail: Mapped[str | None] = mapped_column(String(500))
    detected_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")  # PENDING / CONFIRMED / REJECTED
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("app_user.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)
    review_note: Mapped[str | None] = mapped_column(String(500))
    confirmed_qty: Mapped[float | None] = mapped_column(Float)
    judgment_id: Mapped[int | None] = mapped_column(ForeignKey("defect_judgment.id"))

    lot: Mapped[Lot] = relationship()


class RootCauseConfirmation(Base):
    __tablename__ = "root_cause_confirmation"
    __table_args__ = (Index("ix_rcc_node", "node_type", "node_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    judgment_id: Mapped[int] = mapped_column(ForeignKey("defect_judgment.id"), index=True)
    node_type: Mapped[NodeType] = mapped_column(Enum(NodeType, native_enum=False, length=20))
    node_id: Mapped[int] = mapped_column(Integer)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)  # 0~1
    memo: Mapped[str | None] = mapped_column(Text)
    confirmed_by: Mapped[int | None] = mapped_column(ForeignKey("app_user.id"))
    confirmed_at: Mapped[datetime] = mapped_column(DateTime)

    judgment: Mapped[DefectJudgment] = relationship()


class CandidateSnapshot(Base):
    """판정 저장 시점의 원인 후보 순위. 정확도(Top-k) 지표의 기준이다."""

    __tablename__ = "candidate_snapshot"

    id: Mapped[int] = mapped_column(primary_key=True)
    judgment_id: Mapped[int] = mapped_column(ForeignKey("defect_judgment.id"), index=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime)
    # [{"node": "lot:12", "rank": 1, "score": 0.8, "factors": {...}}, ...]
    ranked: Mapped[list[dict[str, Any]]] = mapped_column(JSON)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("app_user.id"), index=True)
    action: Mapped[str] = mapped_column(String(50))  # create / update / delete / judge / ...
    entity: Mapped[str] = mapped_column(String(50), index=True)
    entity_id: Mapped[str | None] = mapped_column(String(100))
    before: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    at: Mapped[datetime] = mapped_column(DateTime, index=True)
