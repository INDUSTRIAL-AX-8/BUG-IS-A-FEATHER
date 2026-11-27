"""계보 추적, 원인 후보, 잠재 불량 범위 일괄 상태 변경."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import operator, viewer
from app.db import get_db
from app.errors import AppError
from app.models import Lot, LotStatus, Role, User
from app.services import audit, cause_service, genealogy_service as gs, trace_service as ts

router = APIRouter(tags=["계보 추적/원인"])


@router.get("/trace/upstream/{lot_no}")
def trace_upstream(lot_no: str, depth: int | None = Query(default=None, ge=1),
                   db: Session = Depends(get_db), _=Depends(viewer)):
    lot = gs.lot_by_no(db, lot_no)
    return ts.graph_payload(ts.build_upstream(db, lot, depth))


@router.get("/trace/downstream/{node}")
def trace_downstream(
    node: str,
    depth: int | None = Query(default=None, ge=1),
    start: datetime | None = Query(default=None, alias="from"),
    end: datetime | None = Query(default=None, alias="to"),
    at: datetime | None = Query(default=None, description="기준 시각 (from/to 없을 때 ±N일 기간 계산)"),
    db: Session = Depends(get_db),
    _=Depends(viewer),
):
    if (start is None or end is None) and at is not None:
        start, end = ts.default_window(db, at)
    return ts.downstream_payload(db, node, start, end, depth)


@router.get("/causes/{lot_no}")
def causes(lot_no: str, depth: int | None = Query(default=None, ge=1),
           defect_type_id: int | None = None,
           db: Session = Depends(get_db), _=Depends(viewer)):
    lot = gs.lot_by_no(db, lot_no)
    return cause_service.analyze(db, lot, depth, defect_type_id=defect_type_id)


class BulkStatusIn(BaseModel):
    lot_nos: list[str] = Field(min_length=1, max_length=5000)
    status: LotStatus
    reason: str = Field(min_length=1, max_length=500)


@router.post("/lots/bulk-status")
def bulk_status(body: BulkStatusIn, db: Session = Depends(get_db), user: User = Depends(operator)):
    if body.status == LotStatus.DEFECT:
        raise AppError(422, "불량 상태는 불량 판정 화면에서 지정하세요.")
    lots = db.scalars(select(Lot).where(Lot.lot_no.in_(body.lot_nos))).all()
    missing = sorted(set(body.lot_nos) - {l.lot_no for l in lots})
    if missing:
        raise AppError(404, f"없는 LOT 이 있습니다: {', '.join(missing[:10])}", missing)
    releasing = [l for l in lots if l.status == LotStatus.QUARANTINE and body.status != LotStatus.QUARANTINE]
    if releasing and user.role != Role.ADMIN:
        raise AppError(403, "격리 해제는 관리자만 할 수 있습니다.", [l.lot_no for l in releasing][:20])
    changed = []
    for lot in lots:
        if lot.status in (body.status, LotStatus.DEFECT):
            continue  # 불량 LOT 은 판정 취소로만 바뀐다
        before = lot.status.value
        lot.status = body.status
        changed.append({"lot_no": lot.lot_no, "before": before})
    if changed:
        audit.record(db, user, "bulk_status", "lot", None,
                     before={"lots": changed},
                     after={"status": body.status.value, "reason": body.reason, "count": len(changed)})
    db.commit()
    return {"changed": len(changed), "skipped": len(lots) - len(changed), "status": body.status.value}
