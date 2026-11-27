"""요인 분석 API: 설비/공정 단위로 '무엇 때문에 불량이 늘었나'를 자동 판단."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.deps import operator, viewer
from app.db import get_db
from app.models import Equipment, EquipmentEvent, ProcessRun, ProcessStep, User
from app.services import driver_service

router = APIRouter(prefix="/analysis", tags=["요인 분석"])


@router.get("/targets")
def targets(db: Session = Depends(get_db), _=Depends(viewer)):
    """분석할 수 있는 설비 목록 (run 수 포함)."""
    counts = dict(db.execute(select(ProcessRun.equipment_id, func.count()).group_by(ProcessRun.equipment_id)).all())
    steps = dict(db.execute(select(ProcessStep.id, ProcessStep.name)).all())
    events = dict(db.execute(select(EquipmentEvent.equipment_id, func.count()).group_by(EquipmentEvent.equipment_id)).all())
    return [{"equipment_id": e.id, "equipment": e.name, "process_step": steps.get(e.process_step_id),
             "runs": counts.get(e.id, 0), "events": events.get(e.id, 0)}
            for e in db.scalars(select(Equipment).order_by(Equipment.name)) if counts.get(e.id)]


@router.get("/drivers")
def drivers(
    equipment_id: int | None = None,
    process_step_id: int | None = None,
    start: datetime | None = Query(default=None, alias="from"),
    end: datetime | None = Query(default=None, alias="to"),
    db: Session = Depends(get_db),
    _=Depends(viewer),
):
    return driver_service.analyze_equipment(db, equipment_id, process_step_id, start, end)


@router.post("/drivers/apply")
def apply(
    equipment_id: int | None = None,
    process_step_id: int | None = None,
    start: datetime | None = Query(default=None, alias="from"),
    end: datetime | None = Query(default=None, alias="to"),
    db: Session = Depends(get_db),
    user: User = Depends(operator),
):
    """분석 후 학습 범위를 조건 항목에 기록 (허용 범위가 정의되지 않은 항목만, 감사 로그)."""
    r = driver_service.analyze_equipment(db, equipment_id, process_step_id, start, end, apply_learned=True, user=user)
    return {"verdict": r["verdict"], "learned": r["learned"]}
