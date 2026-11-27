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
    # 조건값이 있는 설비만 (요인이 없으면 분석할 것이 없음). 공정 흐름의 주 설비를 맨 앞에
    from app.models import ConditionValue
    from app.services.settings_service import get_setting

    with_conds = set(db.scalars(select(ProcessRun.equipment_id).join(ConditionValue, ConditionValue.process_run_id == ProcessRun.id)
                                .where(ProcessRun.equipment_id.is_not(None)).distinct()))
    mains = {s.get("equipment_id") for line in get_setting(db, "process_lines").values() for s in line if s.get("main")}
    rows = [{"equipment_id": e.id, "equipment": e.name, "process_step": steps.get(e.process_step_id),
             "runs": counts.get(e.id, 0), "events": events.get(e.id, 0), "main": e.id in mains}
            for e in db.scalars(select(Equipment).order_by(Equipment.name)) if counts.get(e.id) and e.id in with_conds]
    return sorted(rows, key=lambda r: (not r["main"], -r["runs"]))


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
