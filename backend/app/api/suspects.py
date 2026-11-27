"""불량 의심 검토: 자동으로 걸린 의심을 공정 단계별로 모아 실무자가 '불량 확정' 또는 '불량 아님'으로 판단한다.
확정된 것만 불량 판정(DefectJudgment)으로 적재되어 원인 분석·정확도·생산 대시보드에 반영된다."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.deps import operator, viewer
from app.db import get_db
from app.errors import AppError, not_found
from app.models import DefectJudgment, DefectSuspect, DefectType, Equipment, Lot, LotStatus, ProcessStep, User
from app.services import audit

router = APIRouter(prefix="/suspects", tags=["불량 의심 검토"])

SOURCE_LABEL = {"CONDITION": "조건 이탈", "SPC": "관리도 위반", "INSPECTION": "검사 불합격", "MANUAL": "수동 등록"}
REJECT_REASONS = ["재검 결과 양품", "센서·측정 오류", "검사 과검출", "기타"]


def _row(s: DefectSuspect, steps: dict, eqs: dict, types: dict, users: dict, lots: dict) -> dict:
    lot = lots.get(s.lot_id)
    return {
        "id": s.id, "lot_id": s.lot_id, "lot_no": lot.lot_no if lot else None, "lot_qty": lot.qty if lot else None,
        "process_step_id": s.process_step_id, "stage": steps.get(s.process_step_id, (None, 0))[0],
        "stage_seq": steps.get(s.process_step_id, (None, 0))[1], "equipment": eqs.get(s.equipment_id),
        "source": s.source, "source_label": SOURCE_LABEL.get(s.source, s.source), "suspected_qty": s.suspected_qty,
        "defect_type_id": s.defect_type_id, "defect_type": types.get(s.defect_type_id), "detail": s.detail,
        "detected_at": s.detected_at, "status": s.status, "reviewed_by": users.get(s.reviewed_by),
        "reviewed_at": s.reviewed_at, "review_note": s.review_note, "confirmed_qty": s.confirmed_qty,
        "judgment_id": s.judgment_id,
    }


def _lookups(db: Session, rows: list[DefectSuspect]):
    steps = {i: (n, q) for i, n, q in db.execute(select(ProcessStep.id, ProcessStep.name, ProcessStep.seq))}
    eqs = dict(db.execute(select(Equipment.id, Equipment.name)).all())
    types = {i: f"{c} {n}" for i, c, n in db.execute(select(DefectType.id, DefectType.code, DefectType.name))}
    users = dict(db.execute(select(User.id, User.username)).all())
    lot_ids = list({r.lot_id for r in rows})
    lots = {l.id: l for l in db.scalars(select(Lot).where(Lot.id.in_(lot_ids or [-1])))}
    return steps, eqs, types, users, lots


@router.get("")
def list_suspects(
    status: str | None = Query(default="PENDING", pattern="^(PENDING|CONFIRMED|REJECTED|ALL)$"),
    process_step_id: int | None = None,
    source: str | None = None,
    lot_no: str | None = None,
    limit: int = Query(default=300, ge=1, le=2000),
    db: Session = Depends(get_db),
    _=Depends(viewer),
):
    stmt = select(DefectSuspect)
    if status and status != "ALL":
        stmt = stmt.where(DefectSuspect.status == status)
    if process_step_id is not None:
        stmt = stmt.where(DefectSuspect.process_step_id == process_step_id)
    if source:
        stmt = stmt.where(DefectSuspect.source == source)
    if lot_no:
        stmt = stmt.where(DefectSuspect.lot_id.in_(select(Lot.id).where(Lot.lot_no.contains(lot_no))))
    rows = db.scalars(stmt.order_by(DefectSuspect.detected_at.desc(), DefectSuspect.id.desc()).limit(limit)).all()
    lk = _lookups(db, rows)
    return [_row(r, *lk) for r in rows]


@router.get("/summary")
def summary(db: Session = Depends(get_db), _=Depends(viewer)):
    """공정 단계 × 출처 × 상태 건수 (공정 순서대로)."""
    steps = {i: (n, q) for i, n, q in db.execute(select(ProcessStep.id, ProcessStep.name, ProcessStep.seq))}
    out: dict = {}
    for step_id, src, st, n, qty in db.execute(
        select(DefectSuspect.process_step_id, DefectSuspect.source, DefectSuspect.status, func.count(),
               func.sum(DefectSuspect.suspected_qty))
        .group_by(DefectSuspect.process_step_id, DefectSuspect.source, DefectSuspect.status)
    ):
        name, seq = steps.get(step_id, ("미지정", 999))
        g = out.setdefault(step_id, {"process_step_id": step_id, "stage": name, "seq": seq, "PENDING": 0, "CONFIRMED": 0,
                                     "REJECTED": 0, "pending_qty": 0.0, "by_source": {}})
        g[st] = g.get(st, 0) + n
        if st == "PENDING":
            g["pending_qty"] += qty or 0
            g["by_source"][SOURCE_LABEL.get(src, src)] = g["by_source"].get(SOURCE_LABEL.get(src, src), 0) + n
    stages = sorted(out.values(), key=lambda g: g["seq"])
    return {"stages": stages, "pending_total": sum(g["PENDING"] for g in stages), "reject_reasons": REJECT_REASONS,
            "sources": SOURCE_LABEL}


class ConfirmIn(BaseModel):
    qty: float | None = Field(default=None, gt=0, description="확정 불량 수량 (없으면 의심 수량)")
    defect_type_id: int | None = None
    memo: str | None = Field(default=None, max_length=500)


class RejectIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class BulkIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=500)
    action: str = Field(pattern="^(confirm|reject)$")
    reason: str | None = None


def _publish(msg: dict) -> None:
    try:
        from app.live.simulator import get_simulator

        get_simulator().publish(msg)
    except Exception:
        pass


def _get_pending(db: Session, sid: int) -> DefectSuspect:
    s = db.get(DefectSuspect, sid)
    if s is None:
        raise not_found("불량 의심")
    if s.status != "PENDING":
        raise AppError(409, "이미 검토된 의심입니다.")
    return s


def confirm_one(db: Session, s: DefectSuspect, body: ConfirmIn, user: User) -> DefectJudgment:
    qty = body.qty if body.qty is not None else s.suspected_qty
    if qty is None:
        raise AppError(422, "조건 이탈·관리도 위반 의심은 재검 결과 불량 수량을 입력해야 확정할 수 있습니다.")
    lot = db.get(Lot, s.lot_id)
    if lot.qty is not None and qty > lot.qty:
        raise AppError(422, f"불량 수량이 생산 수량({lot.qty:g})보다 많습니다.")
    type_id = body.defect_type_id or s.defect_type_id
    if type_id is None:
        raise AppError(422, "불량 유형을 선택하세요.")
    if db.get(DefectType, type_id) is None:
        raise not_found("불량 유형")
    step = db.get(ProcessStep, s.process_step_id) if s.process_step_id else None
    now = datetime.now()
    j = DefectJudgment(lot_id=lot.id, defect_type_id=type_id, judged_by=user.id, judged_at=s.detected_at,
                       defect_qty=qty, attachments=[],
                       memo=f"[{step.name if step else '-'}·{SOURCE_LABEL.get(s.source, s.source)}] 의심 확정: {s.detail or ''}"
                            + (f" / {body.memo}" if body.memo else ""))
    db.add(j)
    db.flush()
    s.status, s.reviewed_by, s.reviewed_at = "CONFIRMED", user.id, now
    s.confirmed_qty, s.judgment_id, s.review_note = qty, j.id, body.memo
    if lot.qty is not None and qty >= lot.qty:
        lot.status = LotStatus.DEFECT
    audit.record(db, user, "confirm_suspect", "defect_suspect", s.id, after={"qty": qty, "judgment_id": j.id})
    return j


def reject_one(db: Session, s: DefectSuspect, reason: str, user: User) -> None:
    s.status, s.reviewed_by, s.reviewed_at, s.review_note = "REJECTED", user.id, datetime.now(), reason
    audit.record(db, user, "reject_suspect", "defect_suspect", s.id, after={"reason": reason})


@router.post("/{suspect_id}/confirm")
def confirm(suspect_id: int, body: ConfirmIn, db: Session = Depends(get_db), user: User = Depends(operator)):
    s = _get_pending(db, suspect_id)
    j = confirm_one(db, s, body, user)
    db.commit()
    lot = db.get(Lot, s.lot_id)
    _publish({"type": "review", "suspect_id": s.id, "lot_id": s.lot_id, "lot_no": lot.lot_no, "status": "CONFIRMED",
              "qty": s.confirmed_qty, "at": s.detected_at})
    return {"id": s.id, "status": s.status, "judgment_id": j.id}


@router.post("/{suspect_id}/reject")
def reject(suspect_id: int, body: RejectIn, db: Session = Depends(get_db), user: User = Depends(operator)):
    s = _get_pending(db, suspect_id)
    reject_one(db, s, body.reason, user)
    db.commit()
    lot = db.get(Lot, s.lot_id)
    _publish({"type": "review", "suspect_id": s.id, "lot_id": s.lot_id, "lot_no": lot.lot_no, "status": "REJECTED",
              "qty": 0, "at": s.detected_at})
    return {"id": s.id, "status": s.status}


@router.post("/bulk")
def bulk(body: BulkIn, db: Session = Depends(get_db), user: User = Depends(operator)):
    """여러 건 한꺼번에. 확정은 의심 수량이 있는 것(검사 불합격)만, 나머지는 건너뜀."""
    done, skipped = [], []
    for sid in body.ids:
        s = db.get(DefectSuspect, sid)
        if s is None or s.status != "PENDING":
            skipped.append(sid)
            continue
        if body.action == "confirm":
            if s.suspected_qty is None or s.defect_type_id is None:
                skipped.append(sid)
                continue
            confirm_one(db, s, ConfirmIn(), user)
        else:
            reject_one(db, s, body.reason or "기타", user)
        done.append(s)
    db.commit()
    for s in done:
        lot = db.get(Lot, s.lot_id)
        _publish({"type": "review", "suspect_id": s.id, "lot_id": s.lot_id, "lot_no": lot.lot_no, "status": s.status,
                  "qty": s.confirmed_qty or 0, "at": s.detected_at})
    return {"done": len(done), "skipped": skipped}
