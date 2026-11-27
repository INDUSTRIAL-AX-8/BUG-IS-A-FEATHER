"""실시간 수집(시연)이 만든 기록 관리: 개수 세기, 지우기.

시연 기록은 실제 기록과 같은 테이블에 쌓이므로 Lot.is_simulated / EquipmentEvent.is_simulated 로 구분한다.
"""

from __future__ import annotations

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.models import (
    CandidateSnapshot,
    ConditionValue,
    DefectJudgment,
    DefectSuspect,
    EquipmentEvent,
    GenealogyEdge,
    Lot,
    LotNote,
    Measurement,
    NodeType,
    ProcessRun,
    RootCauseConfirmation,
    SpcViolation,
)

_CHUNK = 500


def count_simulated(db: Session) -> dict[str, int]:
    return {
        "lots": db.scalar(select(func.count()).select_from(Lot).where(Lot.is_simulated.is_(True))) or 0,
        "equipment_events": db.scalar(
            select(func.count()).select_from(EquipmentEvent).where(EquipmentEvent.is_simulated.is_(True))) or 0,
    }


def purge_simulated(db: Session) -> dict[str, int]:
    """시연 기록(LOT 과 거기 딸린 공정 처리·조건값·측정값·불량 의심·판정·메모, 시연 정지 이벤트)을 지우고
    관리도 위반을 다시 계산한다. 실제 기록은 건드리지 않는다."""
    lot_ids = list(db.scalars(select(Lot.id).where(Lot.is_simulated.is_(True))))
    counts = {"lots": len(lot_ids), "judgments": 0, "suspects": 0, "measurements": 0, "equipment_events": 0}
    purged = set(lot_ids)
    for i in range(0, len(lot_ids), _CHUNK):
        chunk = lot_ids[i:i + _CHUNK]
        run_ids = list(db.scalars(select(ProcessRun.id).where(ProcessRun.lot_id.in_(chunk))))
        judgment_ids = list(db.scalars(select(DefectJudgment.id).where(DefectJudgment.lot_id.in_(chunk))))
        if judgment_ids:
            db.execute(delete(CandidateSnapshot).where(CandidateSnapshot.judgment_id.in_(judgment_ids)))
            db.execute(delete(RootCauseConfirmation).where(RootCauseConfirmation.judgment_id.in_(judgment_ids)))
        counts["suspects"] += db.execute(delete(DefectSuspect).where(DefectSuspect.lot_id.in_(chunk))).rowcount or 0
        counts["judgments"] += db.execute(delete(DefectJudgment).where(DefectJudgment.lot_id.in_(chunk))).rowcount or 0
        # 시연 LOT 을 원인으로 확정한 기록 (다른 판정에서 가리킨 경우)
        db.execute(delete(RootCauseConfirmation).where(RootCauseConfirmation.node_type == NodeType.LOT,
                                                       RootCauseConfirmation.node_id.in_(chunk)))
        db.execute(delete(LotNote).where(LotNote.lot_id.in_(chunk)))
        cond = Measurement.lot_id.in_(chunk)
        if run_ids:
            cond = or_(cond, Measurement.process_run_id.in_(run_ids))
        counts["measurements"] += db.execute(delete(Measurement).where(cond)).rowcount or 0
        if run_ids:
            db.execute(delete(ConditionValue).where(ConditionValue.process_run_id.in_(run_ids)))
        db.execute(delete(ProcessRun).where(ProcessRun.lot_id.in_(chunk)))
        db.execute(delete(GenealogyEdge).where(or_(GenealogyEdge.parent_lot_id.in_(chunk),
                                                   GenealogyEdge.child_lot_id.in_(chunk))))
    if purged:
        # 확인(ack)된 위반은 재계산에서 남으므로, 시연 LOT 이 들어간 위반은 여기서 지운다
        stale = [v.id for v in db.scalars(select(SpcViolation)) if purged.intersection(v.lot_ids or [])]
        for i in range(0, len(stale), _CHUNK):
            db.execute(delete(SpcViolation).where(SpcViolation.id.in_(stale[i:i + _CHUNK])))
        for i in range(0, len(lot_ids), _CHUNK):
            db.execute(delete(Lot).where(Lot.id.in_(lot_ids[i:i + _CHUNK])))
    counts["equipment_events"] = db.execute(
        delete(EquipmentEvent).where(EquipmentEvent.is_simulated.is_(True))).rowcount or 0
    db.commit()

    from app.services.spc_service import recompute_all

    recompute_all(db)
    return counts
