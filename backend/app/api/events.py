"""시간순 통합 이벤트 API — 3D 시뮬레이션 재생과 2D 타임라인이 같은 데이터를 쓴다 (향후 확장 자리).

이벤트 종류: run_start, run_end (ProcessRun), measurement, violation, judgment
각 이벤트는 설비 좌표(pos_x/y/z, nullable)를 함께 실어 3D 쪽이 바로 배치할 수 있게 한다.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import viewer
from app.db import get_db
from app.errors import AppError
from app.models import (
    Characteristic,
    DefectJudgment,
    DefectType,
    Equipment,
    EquipmentEvent,
    Lot,
    Measurement,
    ProcessRun,
    ProcessStep,
    SpcViolation,
)

router = APIRouter(tags=["이벤트(타임라인/3D)"])

EVENT_TYPES = ("run_start", "run_end", "measurement", "violation", "judgment", "equipment_event")


@router.get("/events")
def events(
    start: datetime = Query(alias="from"),
    end: datetime = Query(alias="to"),
    types: str | None = Query(default=None, description="쉼표 구분: run_start,run_end,measurement,violation,judgment"),
    equipment_id: int | None = None,
    limit: int = Query(default=5000, ge=1, le=50000),
    db: Session = Depends(get_db),
    _=Depends(viewer),
):
    if end <= start:
        raise AppError(422, "to 는 from 보다 뒤여야 합니다.")
    wanted = set(types.split(",")) if types else set(EVENT_TYPES)
    bad = wanted - set(EVENT_TYPES)
    if bad:
        raise AppError(422, f"알 수 없는 이벤트 종류: {', '.join(sorted(bad))}")
    eq = {e.id: e for e in db.scalars(select(Equipment))}
    steps = dict(db.execute(select(ProcessStep.id, ProcessStep.name)).all())

    def eq_info(eid):
        e = eq.get(eid)
        return None if e is None else {"id": e.id, "name": e.name, "pos": [e.pos_x, e.pos_y, e.pos_z],
                                       "model_path": e.model_path}

    out: list[dict] = []
    if wanted & {"run_start", "run_end"}:
        stmt = select(ProcessRun, Lot.lot_no).join(Lot, Lot.id == ProcessRun.lot_id).where(
            ProcessRun.started_at <= end, (ProcessRun.ended_at.is_(None)) | (ProcessRun.ended_at >= start))
        if equipment_id is not None:
            stmt = stmt.where(ProcessRun.equipment_id == equipment_id)
        for r, lot_no in db.execute(stmt.limit(limit)):
            base = {"run_id": r.id, "lot_no": lot_no, "step": steps.get(r.process_step_id), "equipment": eq_info(r.equipment_id)}
            if "run_start" in wanted and start <= r.started_at <= end:
                out.append({"type": "run_start", "at": r.started_at, **base})
            if "run_end" in wanted and r.ended_at and start <= r.ended_at <= end:
                out.append({"type": "run_end", "at": r.ended_at, **base})
    if "measurement" in wanted:
        stmt = (select(Measurement, Characteristic.name).join(Characteristic, Characteristic.id == Measurement.characteristic_id)
                .where(Measurement.measured_at >= start, Measurement.measured_at <= end))
        for m, name in db.execute(stmt.limit(limit)):
            out.append({"type": "measurement", "at": m.measured_at, "characteristic": name, "value": m.value,
                        "lot_id": m.lot_id, "run_id": m.process_run_id})
    if "violation" in wanted:
        stmt = (select(SpcViolation, Characteristic.name).join(Characteristic, Characteristic.id == SpcViolation.characteristic_id)
                .where(SpcViolation.occurred_at >= start, SpcViolation.occurred_at <= end))
        for v, name in db.execute(stmt.limit(limit)):
            out.append({"type": "violation", "at": v.occurred_at, "violation_id": v.id, "characteristic": name,
                        "rule_no": v.rule_no, "lot_ids": v.lot_ids})
    if "judgment" in wanted:
        stmt = (select(DefectJudgment, Lot.lot_no, DefectType.code).join(Lot, Lot.id == DefectJudgment.lot_id)
                .join(DefectType, DefectType.id == DefectJudgment.defect_type_id)
                .where(DefectJudgment.judged_at >= start, DefectJudgment.judged_at <= end,
                       DefectJudgment.is_cancelled.is_(False)))
        for j, lot_no, code in db.execute(stmt.limit(limit)):
            out.append({"type": "judgment", "at": j.judged_at, "judgment_id": j.id, "lot_no": lot_no, "defect_type": code})
    if "equipment_event" in wanted:
        stmt = select(EquipmentEvent).where(EquipmentEvent.started_at >= start, EquipmentEvent.started_at <= end)
        if equipment_id is not None:
            stmt = stmt.where(EquipmentEvent.equipment_id == equipment_id)
        for e in db.scalars(stmt.limit(limit)):
            out.append({"type": "equipment_event", "at": e.started_at, "until": e.ended_at, "kind": e.kind,
                        "note": e.note, "equipment": eq_info(e.equipment_id)})
    out.sort(key=lambda e: (e["at"], EVENT_TYPES.index(e["type"])))
    truncated = len(out) > limit
    return {"from": start, "to": end, "count": min(len(out), limit), "truncated": truncated, "events": out[:limit]}
