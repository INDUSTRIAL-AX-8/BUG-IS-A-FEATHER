"""계보 DB 조회. 탐색 자체는 core.genealogy.dag 의 순수 함수가 한다."""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.genealogy.dag import bfs_levels, would_create_cycle
from app.errors import AppError, not_found
from app.models import GenealogyEdge, Lot

_CHUNK = 500  # IN 절 한 번에 넣을 최대 개수 (DB 파라미터 한도 대비)


def _chunks(ids: set[int]):
    items = sorted(ids)
    for i in range(0, len(items), _CHUNK):
        yield items[i : i + _CHUNK]


def parents_of(db: Session):
    def fn(ids: set[int]) -> dict[int, list[int]]:
        out: dict[int, list[int]] = defaultdict(list)
        for chunk in _chunks(ids):
            for child, parent in db.execute(
                select(GenealogyEdge.child_lot_id, GenealogyEdge.parent_lot_id).where(
                    GenealogyEdge.child_lot_id.in_(chunk)
                )
            ):
                out[child].append(parent)
        return out

    return fn


def children_of(db: Session):
    def fn(ids: set[int]) -> dict[int, list[int]]:
        out: dict[int, list[int]] = defaultdict(list)
        for chunk in _chunks(ids):
            for parent, child in db.execute(
                select(GenealogyEdge.parent_lot_id, GenealogyEdge.child_lot_id).where(
                    GenealogyEdge.parent_lot_id.in_(chunk)
                )
            ):
                out[parent].append(child)
        return out

    return fn


def lot_by_no(db: Session, lot_no: str) -> Lot:
    lot = db.scalar(select(Lot).where(Lot.lot_no == lot_no))
    if lot is None:
        raise not_found(f"LOT '{lot_no}'")
    return lot


def traverse(
    db: Session, start_ids: list[int], direction: str, depth: int, max_nodes: int | None = None
) -> tuple[dict[int, int], bool]:
    if direction not in ("up", "down"):
        raise AppError(422, "방향은 up(역방향) 또는 down(정방향)이어야 합니다.")
    fn = parents_of(db) if direction == "up" else children_of(db)
    return bfs_levels(start_ids, fn, depth, max_nodes)


def edges_within(db: Session, lot_ids: set[int]) -> list[GenealogyEdge]:
    """주어진 LOT 집합 내부의 엣지만."""
    result: list[GenealogyEdge] = []
    for chunk in _chunks(lot_ids):
        rows = db.scalars(select(GenealogyEdge).where(GenealogyEdge.child_lot_id.in_(chunk))).all()
        result.extend(e for e in rows if e.parent_lot_id in lot_ids)
    return result


def add_edge(
    db: Session,
    parent: Lot,
    child: Lot,
    input_qty: float | None,
    input_ratio: float | None,
) -> GenealogyEdge:
    if parent.id == child.id:
        raise AppError(409, "자기 자신을 투입할 수 없습니다.")
    if would_create_cycle(parent.id, child.id, children_of(db)):
        raise AppError(
            409,
            f"순환 계보입니다: '{child.lot_no}' 이(가) 이미 '{parent.lot_no}' 의 상위 LOT 입니다. "
            "투입 방향을 확인하세요.",
        )
    exists = db.scalar(
        select(GenealogyEdge).where(
            GenealogyEdge.parent_lot_id == parent.id, GenealogyEdge.child_lot_id == child.id
        )
    )
    if exists:
        raise AppError(409, f"이미 등록된 투입 관계입니다: {parent.lot_no} → {child.lot_no}")
    edge = GenealogyEdge(
        parent_lot_id=parent.id, child_lot_id=child.id, input_qty=input_qty, input_ratio=input_ratio
    )
    db.add(edge)
    db.flush()
    return edge
