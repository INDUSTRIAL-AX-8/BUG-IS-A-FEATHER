"""불량 판정, 원인 확정, 첨부."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.deps import admin, operator, viewer
from app.config import PROJECT_DIR
from app.db import get_db
from app.errors import AppError, not_found
from app.models import (
    CandidateSnapshot,
    ConditionDefinition,
    DefectJudgment,
    DefectType,
    Equipment,
    Lot,
    LotStatus,
    NodeType,
    ProcessStep,
    RootCauseConfirmation,
    User,
    node_key,
    parse_node_key,
)
from app.services import audit, cause_service, genealogy_service as gs

router = APIRouter(prefix="/defects", tags=["불량 판정"])

ATTACH_DIR = PROJECT_DIR / "data" / "attachments"
MAX_ATTACH_BYTES = 20 * 1024 * 1024


class JudgmentIn(BaseModel):
    lot_no: str = Field(min_length=1)
    defect_type_id: int
    memo: str | None = None
    judged_at: datetime | None = None


class RootCauseIn(BaseModel):
    node: str = Field(description="노드 ID 예: lot:12, eq:3, step:2, cond:5")
    confidence: float = Field(default=1.0, ge=0, le=1)
    memo: str | None = None


def _node_label(db: Session, ntype: NodeType, nid: int) -> str | None:
    model = {NodeType.LOT: Lot, NodeType.EQUIPMENT: Equipment, NodeType.STEP: ProcessStep,
             NodeType.CONDITION: ConditionDefinition}[ntype]
    obj = db.get(model, nid)
    if obj is None:
        return None
    return obj.lot_no if ntype == NodeType.LOT else obj.name


def _judgment_out(db: Session, j: DefectJudgment, users: dict[int, str] | None = None) -> dict:
    users = users if users is not None else dict(db.execute(select(User.id, User.username)).all())
    confs = db.scalars(select(RootCauseConfirmation).where(RootCauseConfirmation.judgment_id == j.id)
                       .order_by(RootCauseConfirmation.confirmed_at)).all()
    return {
        "id": j.id, "lot_id": j.lot_id, "lot_no": j.lot.lot_no, "item_type": j.lot.item_type.name,
        "defect_type_id": j.defect_type_id, "defect_type": f"{j.defect_type.code} {j.defect_type.name}",
        # defect_qty 가 null 이면 LOT 전체 불량 (CSV·생산 대시보드와 같은 규칙으로 화면이 lot_qty 를 쓴다)
        "defect_qty": j.defect_qty, "lot_qty": j.lot.qty,
        "judged_by": users.get(j.judged_by), "judged_at": j.judged_at, "memo": j.memo,
        "attachments": j.attachments or [], "is_cancelled": j.is_cancelled,
        "root_causes": [
            {"id": c.id, "node": node_key(c.node_type, c.node_id), "type": c.node_type.value,
             "label": _node_label(db, c.node_type, c.node_id), "confidence": c.confidence,
             "memo": c.memo, "confirmed_by": users.get(c.confirmed_by), "confirmed_at": c.confirmed_at}
            for c in confs
        ],
    }


@router.post("", status_code=201)
def create_judgment(body: JudgmentIn, db: Session = Depends(get_db), user: User = Depends(operator)):
    lot = gs.lot_by_no(db, body.lot_no.strip())
    dtype = db.get(DefectType, body.defect_type_id)
    if dtype is None or not dtype.is_active:
        raise not_found("불량 유형")
    now = datetime.now()
    j = DefectJudgment(lot_id=lot.id, defect_type_id=dtype.id, judged_by=user.id,
                       judged_at=body.judged_at or now, memo=body.memo, attachments=[])
    db.add(j)
    before_status = lot.status.value
    lot.status = LotStatus.DEFECT
    db.flush()
    audit.record(db, user, "judge", "defect_judgment", j.id, before={"lot_status": before_status},
                 after=audit.snapshot(j))
    db.commit()
    # 저장 즉시 원인 후보 계산 + 판정 시점 스냅샷 (정확도 지표 기준)
    result = cause_service.analyze(db, lot, defect_type_id=dtype.id)
    cause_service.save_snapshot(db, j, result, now)
    db.commit()
    db.refresh(j)
    return {"judgment": _judgment_out(db, j), "analysis": result}


@router.get("")
def list_judgments(
    lot_no: str | None = None,
    defect_type_id: int | None = None,
    item_type_id: int | None = None,
    start: datetime | None = Query(default=None, alias="from"),
    end: datetime | None = Query(default=None, alias="to"),
    include_cancelled: bool = False,
    limit: int = Query(default=200, ge=1, le=2000),
    db: Session = Depends(get_db),
    _=Depends(viewer),
):
    stmt = select(DefectJudgment).join(Lot, Lot.id == DefectJudgment.lot_id)
    if lot_no:
        stmt = stmt.where(Lot.lot_no.contains(lot_no))
    if defect_type_id is not None:
        stmt = stmt.where(DefectJudgment.defect_type_id == defect_type_id)
    if item_type_id is not None:
        stmt = stmt.where(Lot.item_type_id == item_type_id)
    if start is not None:
        stmt = stmt.where(DefectJudgment.judged_at >= start)
    if end is not None:
        stmt = stmt.where(DefectJudgment.judged_at <= end)
    if not include_cancelled:
        stmt = stmt.where(DefectJudgment.is_cancelled.is_(False))
    rows = db.scalars(stmt.order_by(DefectJudgment.judged_at.desc()).limit(limit)).all()
    users = dict(db.execute(select(User.id, User.username)).all())
    return [_judgment_out(db, j, users) for j in rows]


@router.get("/export.csv")
def export_csv(
    start: datetime | None = Query(default=None, alias="from"),
    end: datetime | None = Query(default=None, alias="to"),
    db: Session = Depends(get_db),
    _=Depends(viewer),
):
    """적재된 불량 데이터 내려받기 (판정 경로: 의심 확정이면 단계·출처 포함)."""
    import csv
    import io

    from fastapi.responses import Response

    from app.models import DefectSuspect, ProcessStep

    stmt = select(DefectJudgment, Lot, DefectType).join(Lot, Lot.id == DefectJudgment.lot_id)         .join(DefectType, DefectType.id == DefectJudgment.defect_type_id).where(DefectJudgment.is_cancelled.is_(False))
    if start:
        stmt = stmt.where(DefectJudgment.judged_at >= start)
    if end:
        stmt = stmt.where(DefectJudgment.judged_at <= end)
    rows = db.execute(stmt.order_by(DefectJudgment.judged_at)).all()
    users = dict(db.execute(select(User.id, User.username)).all())
    steps = dict(db.execute(select(ProcessStep.id, ProcessStep.name)).all())
    via = {s.judgment_id: s for s in db.scalars(select(DefectSuspect).where(DefectSuspect.judgment_id.is_not(None)))}
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["판정ID", "판정 시각", "LOT", "생산 수량", "불량 수량", "불량 유형 코드", "불량 유형", "발견 단계", "출처", "판정자", "메모"])
    for j, lot, t in rows:
        sus = via.get(j.id)
        w.writerow([j.id, j.judged_at.strftime("%Y-%m-%d %H:%M"), lot.lot_no, lot.qty if lot.qty is not None else "",
                    j.defect_qty if j.defect_qty is not None else (lot.qty or 1), t.code, t.name,
                    steps.get(sus.process_step_id, "") if sus else "이력·직접 등록", sus.source if sus else "",
                    users.get(j.judged_by, ""), j.memo or ""])
    return Response(buf.getvalue().encode("utf-8-sig"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="defect_data.csv"'})


@router.get("/summary")
def summary(db: Session = Depends(get_db), _=Depends(viewer)):
    total = db.scalar(select(func.count()).select_from(DefectJudgment).where(DefectJudgment.is_cancelled.is_(False))) or 0
    confirmed = db.scalar(
        select(func.count(func.distinct(RootCauseConfirmation.judgment_id)))
    ) or 0
    by_type = db.execute(
        select(DefectType.code, DefectType.name, func.count())
        .join(DefectJudgment, DefectJudgment.defect_type_id == DefectType.id)
        .where(DefectJudgment.is_cancelled.is_(False))
        .group_by(DefectType.code, DefectType.name)
    ).all()
    return {"total": total, "confirmed": confirmed,
            "by_type": [{"code": c, "name": n, "count": k} for c, n, k in by_type]}


@router.get("/{judgment_id}")
def get_judgment(judgment_id: int, db: Session = Depends(get_db), _=Depends(viewer)):
    j = db.get(DefectJudgment, judgment_id)
    if j is None:
        raise not_found("불량 판정")
    out = _judgment_out(db, j)
    snap = db.scalar(select(CandidateSnapshot).where(CandidateSnapshot.judgment_id == j.id)
                     .order_by(CandidateSnapshot.computed_at).limit(1))
    out["snapshot"] = None if snap is None else {"computed_at": snap.computed_at, "ranked": snap.ranked[:10]}
    return out


@router.post("/{judgment_id}/root-cause", status_code=201)
def confirm_root_cause(judgment_id: int, body: RootCauseIn, db: Session = Depends(get_db),
                       user: User = Depends(operator)):
    j = db.get(DefectJudgment, judgment_id)
    if j is None:
        raise not_found("불량 판정")
    if j.is_cancelled:
        raise AppError(409, "취소된 판정에는 원인을 확정할 수 없습니다.")
    try:
        ntype, nid = parse_node_key(body.node)
    except ValueError as exc:
        raise AppError(422, str(exc)) from exc
    if _node_label(db, ntype, nid) is None:
        raise not_found("원인 노드")
    c = RootCauseConfirmation(judgment_id=j.id, node_type=ntype, node_id=nid, confidence=body.confidence,
                              memo=body.memo, confirmed_by=user.id, confirmed_at=datetime.now())
    db.add(c)
    db.flush()
    audit.record(db, user, "confirm_root_cause", "root_cause_confirmation", c.id, after=audit.snapshot(c))
    db.commit()
    return _judgment_out(db, j)


@router.post("/{judgment_id}/cancel")
def cancel_judgment(judgment_id: int, db: Session = Depends(get_db), user: User = Depends(admin)):
    j = db.get(DefectJudgment, judgment_id)
    if j is None:
        raise not_found("불량 판정")
    if j.is_cancelled:
        raise AppError(409, "이미 취소된 판정입니다.")
    before = audit.snapshot(j)
    j.is_cancelled = True
    j.cancelled_by = user.id
    j.cancelled_at = datetime.now()
    others = db.scalar(select(func.count()).where(
        DefectJudgment.lot_id == j.lot_id, DefectJudgment.id != j.id, DefectJudgment.is_cancelled.is_(False)))
    if not others and j.lot.status == LotStatus.DEFECT:
        j.lot.status = LotStatus.NORMAL
    audit.record(db, user, "cancel_judgment", "defect_judgment", j.id, before=before, after=audit.snapshot(j))
    db.commit()
    return _judgment_out(db, j)


_SAFE = re.compile(r"[^0-9A-Za-z가-힣._-]+")


@router.post("/{judgment_id}/attachments")
async def add_attachment(judgment_id: int, file: UploadFile = File(...), db: Session = Depends(get_db),
                         user: User = Depends(operator)):
    j = db.get(DefectJudgment, judgment_id)
    if j is None:
        raise not_found("불량 판정")
    data = await file.read()
    if len(data) > MAX_ATTACH_BYTES:
        raise AppError(413, "첨부 파일은 20MB 이하만 가능합니다.")
    name = _SAFE.sub("_", Path(file.filename or "file").name)[:120] or "file"
    stored = f"{judgment_id}_{uuid.uuid4().hex[:8]}_{name}"
    ATTACH_DIR.mkdir(parents=True, exist_ok=True)
    (ATTACH_DIR / stored).write_bytes(data)
    j.attachments = [*(j.attachments or []), stored]
    audit.record(db, user, "attach", "defect_judgment", j.id, after={"file": stored})
    db.commit()
    return {"attachments": j.attachments}


@router.get("/{judgment_id}/attachments/{name}")
def get_attachment(judgment_id: int, name: str, db: Session = Depends(get_db), _=Depends(viewer)):
    j = db.get(DefectJudgment, judgment_id)
    if j is None or name not in (j.attachments or []):
        raise not_found("첨부 파일")
    path = (ATTACH_DIR / name).resolve()
    if ATTACH_DIR.resolve() not in path.parents or not path.is_file():
        raise not_found("첨부 파일")
    return FileResponse(path, filename=name.split("_", 2)[-1])

