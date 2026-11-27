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
    attachments: Mapped[list[str]] = mapped_column(JSON, default=list)
    is_cancelled: Mapped[bool] = mapped_column(Boolean, default=False)
    cancelled_by: Mapped[int | None] = mapped_column(ForeignKey("app_user.id"))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime)

    lot: Mapped[Lot] = relationship()
    defect_type: Mapped[DefectType] = relationship()


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
