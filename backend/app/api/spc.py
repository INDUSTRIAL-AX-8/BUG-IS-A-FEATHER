"""SPC API: 관리도, 관리한계 고정, 위반 조회/확인."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.deps import admin, operator, viewer
from app.core.spc.nelson import describe, merge_config
from app.db import get_db
from app.errors import AppError, not_found
from app.models import (
    Characteristic,
    ChartType,
    ControlLimit,
    ItemType,
    Lot,
    Measurement,
    ProcessStep,
    SpcViolation,
    User,
)
from app.services import audit, spc_service
from app.services.settings_service import get_setting

router = APIRouter(prefix="/spc", tags=["SPC"])


class LimitIn(BaseModel):
    baseline_from: datetime
    baseline_to: datetime
    chart_type: ChartType | None = None


def _owner_names(db: Session) -> tuple[dict[int, str], dict[int, str]]:
    return (
        dict(db.execute(select(ItemType.id, ItemType.name)).all()),
        dict(db.execute(select(ProcessStep.id, ProcessStep.name)).all()),
    )


@router.get("/characteristics")
def characteristic_summary(db: Session = Depends(get_db), _=Depends(viewer)):
    items, steps = _owner_names(db)
    counts = dict(db.execute(
        select(Measurement.characteristic_id, func.count()).group_by(Measurement.characteristic_id)
    ).all())
    open_v = dict(db.execute(
        select(SpcViolation.characteristic_id, func.count())
        .where(SpcViolation.acknowledged_at.is_(None))
        .group_by(SpcViolation.characteristic_id)
    ).all())
    out = []
    for c in db.scalars(select(Characteristic).order_by(Characteristic.name)):
        lim = spc_service.locked_limit(db, c.id)
        out.append({
            "id": c.id, "name": c.name, "unit": c.unit, "is_active": c.is_active,
            "owner": items.get(c.item_type_id) if c.item_type_id else steps.get(c.process_step_id),
            "owner_kind": "item_type" if c.item_type_id else "process_step",
            "chart_type": spc_service.resolved_chart_type(db, c),
            "measurements": counts.get(c.id, 0),
            "open_violations": open_v.get(c.id, 0),
            "limit_version": lim.version if lim else None,
        })
    return out


@router.get("/{char_id}/chart")
def chart(
    char_id: int,
    start: datetime | None = Query(default=None, alias="from"),
    end: datetime | None = Query(default=None, alias="to"),
    db: Session = Depends(get_db),
    _=Depends(viewer),
):
    return spc_service.chart_payload(db, char_id, start, end)


@router.get("/{char_id}/limits")
def limit_history(char_id: int, db: Session = Depends(get_db), _=Depends(viewer)):
    return [
        {
            "id": r.id, "version": r.version, "chart_type": r.chart_type, "center": r.center,
            "ucl": r.ucl, "lcl": r.lcl, "sigma": r.sigma, "baseline_from": r.baseline_from,
            "baseline_to": r.baseline_to, "n_subgroups": r.n_subgroups, "created_at": r.created_at,
            "created_by": r.created_by, "is_locked": r.is_locked,
        }
        for r in db.scalars(
            select(ControlLimit).where(ControlLimit.characteristic_id == char_id)
            .order_by(ControlLimit.version.desc())
        )
    ]


@router.post("/{char_id}/limits")
def set_limits(char_id: int, body: LimitIn, db: Session = Depends(get_db), user: User = Depends(admin)):
    char = db.get(Characteristic, char_id)
    if char is None:
        raise not_found("측정 특성")
    row = spc_service.lock_limits(db, char, body.baseline_from, body.baseline_to, user, body.chart_type)
    return {"id": row.id, "version": row.version, "center": row.center, "ucl": row.ucl, "lcl": row.lcl}


@router.post("/recompute")
def recompute(char_id: int | None = None, db: Session = Depends(get_db), _=Depends(operator)):
    if char_id is not None:
        char = db.get(Characteristic, char_id)
        if char is None:
            raise not_found("측정 특성")
        return {"violations": {char_id: spc_service.recompute_violations(db, char)}}
    return {"violations": spc_service.recompute_all(db)}


def violation_rows(
    db: Session,
    characteristic_id: int | None = None,
    acknowledged: bool | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    lot_id: int | None = None,
    limit: int = 200,
) -> list[dict]:
    stmt = select(SpcViolation, Characteristic).join(
        Characteristic, Characteristic.id == SpcViolation.characteristic_id
    )
    if characteristic_id is not None:
        stmt = stmt.where(SpcViolation.characteristic_id == characteristic_id)
    if acknowledged is True:
        stmt = stmt.where(SpcViolation.acknowledged_at.is_not(None))
    elif acknowledged is False:
        stmt = stmt.where(SpcViolation.acknowledged_at.is_(None))
    if start is not None:
        stmt = stmt.where(SpcViolation.occurred_at >= start)
    if end is not None:
        stmt = stmt.where(SpcViolation.occurred_at <= end)
    rows = db.execute(stmt.order_by(SpcViolation.occurred_at.desc()).limit(limit * (5 if lot_id else 1))).all()
    if lot_id is not None:
        rows = [r for r in rows if lot_id in (r[0].lot_ids or [])][:limit]
    items, steps = _owner_names(db)
    defaults = get_setting(db, "nelson_rules")
    lot_ids = {lid for v, _ in rows for lid in (v.lot_ids or [])}
    lot_nos = dict(db.execute(select(Lot.id, Lot.lot_no).where(Lot.id.in_(lot_ids or {-1}))).all())
    out = []
    for v, c in rows:
        cfg = merge_config(defaults, c.rule_config)
        out.append({
            "id": v.id, "characteristic_id": c.id, "characteristic": c.name,
            "owner": items.get(c.item_type_id) if c.item_type_id else steps.get(c.process_step_id),
            "rule_no": v.rule_no, "description": describe(v.rule_no, cfg[v.rule_no]["params"]),
            "occurred_at": v.occurred_at, "detected_at": v.detected_at,
            "point_count": len(v.points or []),
            "lot_ids": v.lot_ids, "lot_nos": [lot_nos.get(i) for i in (v.lot_ids or [])][:20],
            "acknowledged_at": v.acknowledged_at, "acknowledged_by": v.acknowledged_by,
        })
    return out


@router.get("/violations")
def list_violations(
    characteristic_id: int | None = None,
    acknowledged: bool | None = None,
    start: datetime | None = Query(default=None, alias="from"),
    end: datetime | None = Query(default=None, alias="to"),
    limit: int = Query(default=200, ge=1, le=2000),
    db: Session = Depends(get_db),
    _=Depends(viewer),
):
    return violation_rows(db, characteristic_id, acknowledged, start, end, limit=limit)


@router.post("/violations/{violation_id}/ack")
def acknowledge(violation_id: int, db: Session = Depends(get_db), user: User = Depends(operator)):
    v = db.get(SpcViolation, violation_id)
    if v is None:
        raise not_found("SPC 위반")
    if v.acknowledged_at is not None:
        raise AppError(409, "이미 확인된 위반입니다.")
    before = audit.snapshot(v)
    v.acknowledged_at = datetime.now()
    v.acknowledged_by = user.id
    audit.record(db, user, "acknowledge", "spc_violation", v.id, before=before, after=audit.snapshot(v))
    db.commit()
    return {"id": v.id, "acknowledged_at": v.acknowledged_at}
