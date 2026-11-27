"""LOT, 계보, 공정 처리, 측정값 조회 API."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.auth.deps import operator, viewer
from app.db import get_db
from app.errors import AppError, not_found
from app.models import Characteristic, ItemType, Lot, LotNote, LotStatus, Measurement, ProcessRun, Role, User
from app.schemas.lot import (
    EdgeIn,
    EdgeOut,
    GenealogyGraph,
    LotDetail,
    LotIn,
    LotOut,
    MeasurementOut,
    ProcessRunOut,
)
from app.services import audit, genealogy_service as gs, lot_service as ls
from app.services.settings_service import get_setting

router = APIRouter(tags=["LOT/계보"])


@router.get("/lots", response_model=list[LotOut])
def list_lots(
    q: str | None = Query(default=None, description="LOT 번호 일부"),
    item_type_id: int | None = None,
    status: LotStatus | None = None,
    has_note: bool = Query(default=False, description="메모가 있는 LOT 만"),
    created_from: datetime | None = Query(default=None, alias="from"),
    created_to: datetime | None = Query(default=None, alias="to"),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _=Depends(viewer),
):
    stmt = select(Lot)
    if q:
        stmt = stmt.where(or_(Lot.lot_no.contains(q),
                              Lot.id.in_(select(LotNote.lot_id).where(LotNote.text.contains(q)))))
    if has_note:
        stmt = stmt.where(Lot.id.in_(select(LotNote.lot_id)))
    if item_type_id is not None:
        stmt = stmt.where(Lot.item_type_id == item_type_id)
    if status is not None:
        stmt = stmt.where(Lot.status == status)
    if created_from is not None:
        stmt = stmt.where(Lot.created_at >= created_from)
    if created_to is not None:
        stmt = stmt.where(Lot.created_at <= created_to)
    stmt = stmt.order_by(Lot.created_at.desc(), Lot.id.desc()).limit(limit).offset(offset)
    return ls.lots_out(db, db.scalars(stmt).all())


@router.post("/lots", response_model=LotOut, status_code=201)
def create_lot(body: LotIn, db: Session = Depends(get_db), user: User = Depends(operator)):
    if db.get(ItemType, body.item_type_id) is None:
        raise not_found("부품 종류")
    if db.scalar(select(Lot.id).where(Lot.lot_no == body.lot_no)):
        raise AppError(409, f"이미 등록된 LOT 번호입니다: {body.lot_no}")
    lot = Lot(**body.model_dump())
    db.add(lot)
    db.flush()
    audit.record(db, user, "create", "lot", lot.id, after=audit.snapshot(lot))
    db.commit()
    db.refresh(lot)
    return ls.lot_out(lot)


class NoteIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


def _note_out(n: LotNote, users: dict[int, str]) -> dict:
    return {"id": n.id, "lot_id": n.lot_id, "text": n.text, "author": users.get(n.author_id),
            "author_id": n.author_id, "created_at": n.created_at}


@router.get("/lots/search")
def search_lots(q: str = Query(min_length=1), limit: int = Query(default=10, ge=1, le=50),
                db: Session = Depends(get_db), _=Depends(viewer)):
    """상단 검색창: LOT 번호 일부 또는 메모 내용. 정확히 일치하는 LOT 을 맨 앞에."""
    text = q.strip()
    by_no = db.scalars(select(Lot).where(Lot.lot_no.contains(text)).order_by(Lot.created_at.desc()).limit(limit)).all()
    by_note = db.scalars(select(Lot).where(Lot.id.in_(select(LotNote.lot_id).where(LotNote.text.contains(text))))
                         .order_by(Lot.created_at.desc()).limit(limit)).all()
    seen, merged = set(), []
    for l in sorted(by_no, key=lambda x: x.lot_no != text) + by_note:
        if l.id not in seen:
            seen.add(l.id)
            merged.append(l)
    out = ls.lots_out(db, merged[:limit])
    note_hits = {l.id for l in by_note}
    return [{**o.model_dump(), "matched_note": o.id in note_hits} for o in out]


@router.get("/lots/notes/feed")
def notes_feed(q: str | None = None, limit: int = Query(default=200, ge=1, le=2000),
               db: Session = Depends(get_db), _=Depends(viewer)):
    """메모된 제품 모아보기: 메모가 있는 LOT 을 최근 메모 순으로, LOT 별 메모 묶음."""
    stmt = select(LotNote, Lot).join(Lot, Lot.id == LotNote.lot_id)
    if q:
        stmt = stmt.where(or_(LotNote.text.contains(q), Lot.lot_no.contains(q)))
    rows = db.execute(stmt.order_by(LotNote.created_at.desc()).limit(limit)).all()
    users = dict(db.execute(select(User.id, User.username)).all())
    groups: dict[int, dict] = {}
    for n, lot in rows:
        g = groups.get(lot.id)
        if g is None:
            g = groups[lot.id] = {**ls.lot_out(lot).model_dump(), "notes": [], "last_at": n.created_at}
        g["notes"].append(_note_out(n, users))
    return list(groups.values())


@router.get("/lots/{lot_no}/notes")
def lot_notes(lot_no: str, db: Session = Depends(get_db), _=Depends(viewer)):
    lot = gs.lot_by_no(db, lot_no)
    users = dict(db.execute(select(User.id, User.username)).all())
    rows = db.scalars(select(LotNote).where(LotNote.lot_id == lot.id).order_by(LotNote.created_at.desc())).all()
    return [_note_out(n, users) for n in rows]


@router.post("/lots/{lot_no}/notes", status_code=201)
def add_note(lot_no: str, body: NoteIn, db: Session = Depends(get_db), user: User = Depends(operator)):
    lot = gs.lot_by_no(db, lot_no)
    n = LotNote(lot_id=lot.id, text=body.text.strip(), author_id=user.id, created_at=datetime.now())
    db.add(n)
    db.flush()
    audit.record(db, user, "create", "lot_note", n.id, after={"lot_no": lot.lot_no, "text": n.text})
    db.commit()
    return _note_out(n, {user.id: user.username})


@router.delete("/lots/notes/{note_id}", status_code=204)
def delete_note(note_id: int, db: Session = Depends(get_db), user: User = Depends(operator)):
    n = db.get(LotNote, note_id)
    if n is None:
        raise not_found("메모")
    if n.author_id != user.id and user.role != Role.ADMIN:
        raise AppError(403, "본인이 쓴 메모만 지울 수 있습니다 (관리자는 모두 가능).")
    audit.record(db, user, "delete", "lot_note", n.id, before={"text": n.text, "lot_id": n.lot_id})
    db.delete(n)
    db.commit()


@router.get("/lots/{lot_no}", response_model=LotDetail)
def get_lot(lot_no: str, db: Session = Depends(get_db), _=Depends(viewer)):
    return ls.lot_detail(db, gs.lot_by_no(db, lot_no))


@router.get("/lots/{lot_no}/runs", response_model=list[ProcessRunOut])
def lot_runs(lot_no: str, db: Session = Depends(get_db), _=Depends(viewer)):
    return ls.runs_for_lots(db, [gs.lot_by_no(db, lot_no).id])


@router.get("/lots/{lot_no}/measurements", response_model=list[MeasurementOut])
def lot_measurements(lot_no: str, db: Session = Depends(get_db), _=Depends(viewer)):
    lot = gs.lot_by_no(db, lot_no)
    run_ids = select(ProcessRun.id).where(ProcessRun.lot_id == lot.id)
    stmt = (
        select(Measurement)
        .where(or_(Measurement.lot_id == lot.id, Measurement.process_run_id.in_(run_ids)))
        .order_by(Measurement.measured_at)
    )
    return db.scalars(stmt).all()


@router.get("/lots/{lot_no}/genealogy", response_model=GenealogyGraph)
def lot_genealogy(
    lot_no: str,
    direction: str = Query(default="up", pattern="^(up|down)$"),
    depth: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    _=Depends(viewer),
):
    trace = get_setting(db, "trace")
    depth = min(depth or trace["default_depth"], trace["max_depth"])
    lot = gs.lot_by_no(db, lot_no)
    reached, truncated = gs.traverse(db, [lot.id], direction, depth, trace["max_nodes"])
    lots = db.scalars(select(Lot).where(Lot.id.in_(list(reached)))).all()
    edges = gs.edges_within(db, set(reached))
    return GenealogyGraph(
        root_lot_id=lot.id,
        direction=direction,
        depth=depth,
        truncated=truncated,
        lots=ls.lots_out(db, lots),
        edges=[EdgeOut.model_validate(e) for e in edges],
    )


@router.post("/genealogy/edges", response_model=EdgeOut, status_code=201)
def create_edge(body: EdgeIn, db: Session = Depends(get_db), user: User = Depends(operator)):
    parent = gs.lot_by_no(db, body.parent_lot_no)
    child = gs.lot_by_no(db, body.child_lot_no)
    edge = gs.add_edge(db, parent, child, body.input_qty, body.input_ratio)
    audit.record(db, user, "create", "genealogy_edge", edge.id, after=audit.snapshot(edge))
    db.commit()
    return edge


@router.get("/measurements", response_model=list[MeasurementOut])
def list_measurements(
    characteristic_id: int,
    measured_from: datetime | None = Query(default=None, alias="from"),
    measured_to: datetime | None = Query(default=None, alias="to"),
    limit: int = Query(default=5000, ge=1, le=50000),
    db: Session = Depends(get_db),
    _=Depends(viewer),
):
    if db.get(Characteristic, characteristic_id) is None:
        raise not_found("측정 특성")
    stmt = select(Measurement).where(Measurement.characteristic_id == characteristic_id)
    if measured_from is not None:
        stmt = stmt.where(Measurement.measured_at >= measured_from)
    if measured_to is not None:
        stmt = stmt.where(Measurement.measured_at <= measured_to)
    stmt = stmt.order_by(Measurement.measured_at, Measurement.id).limit(limit)
    return db.scalars(stmt).all()
