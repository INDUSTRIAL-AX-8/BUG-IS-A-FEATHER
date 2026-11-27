"""LOT, 계보, 공정 처리, 측정값 조회 API."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.auth.deps import operator, viewer
from app.db import get_db
from app.errors import AppError, not_found
from app.models import Characteristic, ItemType, Lot, LotStatus, Measurement, ProcessRun, User
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
    created_from: datetime | None = Query(default=None, alias="from"),
    created_to: datetime | None = Query(default=None, alias="to"),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _=Depends(viewer),
):
    stmt = select(Lot)
    if q:
        stmt = stmt.where(Lot.lot_no.contains(q))
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
