"""LOT 조회 조립."""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    ConditionDefinition,
    ConditionValue,
    DefectJudgment,
    Equipment,
    GenealogyEdge,
    ItemType,
    Lot,
    ProcessRun,
    ProcessStep,
)
from app.schemas.lot import ConditionValueOut, LotDetail, LotOut, ProcessRunOut


def lot_out(lot: Lot, item_names: dict[int, str] | None = None) -> LotOut:
    name = item_names.get(lot.item_type_id) if item_names else lot.item_type.name
    return LotOut(
        id=lot.id,
        lot_no=lot.lot_no,
        item_type_id=lot.item_type_id,
        item_type_name=name,
        created_at=lot.created_at,
        qty=lot.qty,
        status=lot.status,
    )


def item_type_names(db: Session) -> dict[int, str]:
    return dict(db.execute(select(ItemType.id, ItemType.name)).all())


def lots_out(db: Session, lots: Iterable[Lot]) -> list[LotOut]:
    names = item_type_names(db)
    return [lot_out(l, names) for l in lots]


def lot_detail(db: Session, lot: Lot) -> LotDetail:
    parents = db.scalar(select(func.count()).where(GenealogyEdge.child_lot_id == lot.id)) or 0
    children = db.scalar(select(func.count()).where(GenealogyEdge.parent_lot_id == lot.id)) or 0
    judgments = (
        db.scalar(
            select(func.count()).where(
                DefectJudgment.lot_id == lot.id, DefectJudgment.is_cancelled.is_(False)
            )
        )
        or 0
    )
    base = lot_out(lot).model_dump()
    return LotDetail(**base, parent_count=parents, child_count=children, judgment_count=judgments)


def is_out_of_range(value: float, lo: float | None, hi: float | None) -> bool:
    return (lo is not None and value < lo) or (hi is not None and value > hi)


def runs_for_lots(db: Session, lot_ids: list[int]) -> list[ProcessRunOut]:
    if not lot_ids:
        return []
    runs = db.execute(
        select(ProcessRun, ProcessStep.name, Equipment.name)
        .join(ProcessStep, ProcessStep.id == ProcessRun.process_step_id)
        .outerjoin(Equipment, Equipment.id == ProcessRun.equipment_id)
        .where(ProcessRun.lot_id.in_(lot_ids))
        .order_by(ProcessRun.started_at)
    ).all()
    run_ids = [r.id for r, _, _ in runs]
    conds: dict[int, list[ConditionValueOut]] = {rid: [] for rid in run_ids}
    if run_ids:
        for cv, cd in db.execute(
            select(ConditionValue, ConditionDefinition)
            .join(ConditionDefinition, ConditionDefinition.id == ConditionValue.condition_definition_id)
            .where(ConditionValue.process_run_id.in_(run_ids))
        ):
            conds[cv.process_run_id].append(
                ConditionValueOut(
                    condition_definition_id=cd.id,
                    name=cd.name,
                    unit=cd.unit,
                    value=cv.value,
                    min_value=cd.min_value,
                    max_value=cd.max_value,
                    out_of_range=is_out_of_range(cv.value, cd.min_value, cd.max_value),
                )
            )
    return [
        ProcessRunOut(
            id=r.id,
            lot_id=r.lot_id,
            process_step_id=r.process_step_id,
            process_step_name=step_name,
            equipment_id=r.equipment_id,
            equipment_name=eq_name,
            started_at=r.started_at,
            ended_at=r.ended_at,
            operator_id=r.operator_id,
            conditions=conds[r.id],
        )
        for r, step_name, eq_name in runs
    ]
