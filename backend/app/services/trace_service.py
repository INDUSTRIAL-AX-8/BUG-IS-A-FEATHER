"""계보 추적 그래프 구성: LOT / 공정 / 설비 / 조건 노드.

역방향: 불량 LOT → 상위 LOT(GenealogyEdge) + 각 LOT 의 ProcessRun(공정, 설비, 조건값)
정방향: 원인 후보 노드 → 영향받은 하위 LOT
  - LOT 노드: 하위 계보 전체
  - 설비/공정 노드: 기준 시각 ±N일(설정) 안에 그 설비/공정에서 처리된 LOT 의 하위 전체
  - 조건 노드: 같은 기간에 그 조건이 허용 범위를 벗어난 run 의 LOT 하위 전체
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.errors import AppError, not_found
from app.models import (
    ConditionDefinition,
    ConditionValue,
    Equipment,
    ItemCategory,
    ItemType,
    Lot,
    NodeType,
    ProcessRun,
    ProcessStep,
    node_key,
    parse_node_key,
)
from app.services import genealogy_service as gs
from app.services.lot_service import is_out_of_range
from app.services.settings_service import get_setting


@dataclass
class RunInfo:
    id: int
    lot_id: int
    step_id: int
    equipment_id: int | None
    started_at: datetime
    out_of_range: dict[int, float] = field(default_factory=dict)  # cond_def_id → value
    conditions: dict[int, float] = field(default_factory=dict)


@dataclass
class TraceGraph:
    root_lot_id: int
    direction: str
    depth: int
    truncated: bool
    lots: dict[int, Lot]
    lot_depth: dict[int, int]
    edges: list  # GenealogyEdge
    runs: list[RunInfo]
    steps: dict[int, ProcessStep]
    equipment: dict[int, Equipment]
    conditions: dict[int, ConditionDefinition]
    item_types: dict[int, ItemType]

    def runs_by_node(self) -> dict[str, list[RunInfo]]:
        out: dict[str, list[RunInfo]] = defaultdict(list)
        for r in self.runs:
            out[node_key(NodeType.LOT, r.lot_id)].append(r)
            out[node_key(NodeType.STEP, r.step_id)].append(r)
            if r.equipment_id:
                out[node_key(NodeType.EQUIPMENT, r.equipment_id)].append(r)
            for cid in r.conditions:
                out[node_key(NodeType.CONDITION, cid)].append(r)
        return out


def load_runs(db: Session, lot_ids: list[int] | set[int]) -> list[RunInfo]:
    ids = list(lot_ids)
    if not ids:
        return []
    runs: dict[int, RunInfo] = {}
    for chunk in (ids[i : i + 500] for i in range(0, len(ids), 500)):
        for r in db.scalars(select(ProcessRun).where(ProcessRun.lot_id.in_(chunk))):
            runs[r.id] = RunInfo(r.id, r.lot_id, r.process_step_id, r.equipment_id, r.started_at)
    _attach_conditions(db, runs)
    return sorted(runs.values(), key=lambda r: r.started_at)


def _attach_conditions(db: Session, runs: dict[int, RunInfo]) -> None:
    if not runs:
        return
    defs = {d.id: d for d in db.scalars(select(ConditionDefinition))}
    ids = list(runs)
    for chunk in (ids[i : i + 500] for i in range(0, len(ids), 500)):
        for cv in db.scalars(select(ConditionValue).where(ConditionValue.process_run_id.in_(chunk))):
            d = defs.get(cv.condition_definition_id)
            run = runs[cv.process_run_id]
            run.conditions[cv.condition_definition_id] = cv.value
            if d and is_out_of_range(cv.value, *d.effective_range()[:2]):
                run.out_of_range[d.id] = cv.value


def build_upstream(db: Session, lot: Lot, depth: int | None = None) -> TraceGraph:
    trace = get_setting(db, "trace")
    depth = min(depth or trace["default_depth"], trace["max_depth"])
    lot_depth, truncated = gs.traverse(db, [lot.id], "up", depth, trace["max_nodes"])
    lots = {l.id: l for l in db.scalars(select(Lot).where(Lot.id.in_(list(lot_depth))))}
    edges = gs.edges_within(db, set(lots))
    runs = load_runs(db, set(lots))
    return TraceGraph(
        root_lot_id=lot.id, direction="up", depth=depth, truncated=truncated, lots=lots,
        lot_depth=lot_depth, edges=edges, runs=runs,
        steps={s.id: s for s in db.scalars(select(ProcessStep))},
        equipment={e.id: e for e in db.scalars(select(Equipment))},
        conditions={c.id: c for c in db.scalars(select(ConditionDefinition))},
        item_types={i.id: i for i in db.scalars(select(ItemType))},
    )


def graph_payload(g: TraceGraph, scores: dict[str, dict] | None = None) -> dict:
    """Cytoscape 용 nodes/edges. scores: {node_key: {"score", "rank"}}"""
    scores = scores or {}
    nodes: dict[str, dict] = {}
    edges: dict[str, dict] = {}

    def add_node(key: str, ntype: NodeType, label: str, sub: str = "", **extra):
        if key in nodes:
            return
        s = scores.get(key, {})
        nodes[key] = {"id": key, "type": ntype.value, "label": label, "sub": sub,
                      "score": s.get("score", 0.0), "rank": s.get("rank"), **extra}

    def add_edge(src: str, dst: str, kind: str, **extra):
        eid = f"{src}->{dst}"
        if eid not in edges:
            edges[eid] = {"id": eid, "source": src, "target": dst, "kind": kind, **extra}

    for lot in g.lots.values():
        it = g.item_types.get(lot.item_type_id)
        add_node(node_key(NodeType.LOT, lot.id), NodeType.LOT, lot.lot_no, it.name if it else "",
                 status=lot.status.value, is_root=lot.id == g.root_lot_id, item_type_id=lot.item_type_id,
                 category=it.category.value if it else None, depth=g.lot_depth.get(lot.id),
                 created_at=lot.created_at.isoformat())
    for e in g.edges:
        add_edge(node_key(NodeType.LOT, e.parent_lot_id), node_key(NodeType.LOT, e.child_lot_id),
                 "genealogy", ratio=e.input_ratio, qty=e.input_qty)

    for r in g.runs:
        step = g.steps.get(r.step_id)
        skey = node_key(NodeType.STEP, r.step_id)
        add_node(skey, NodeType.STEP, step.name if step else f"공정 {r.step_id}", "공정",
                 process_step_id=r.step_id)
        lkey = node_key(NodeType.LOT, r.lot_id)
        if r.equipment_id:
            eq = g.equipment.get(r.equipment_id)
            ekey = node_key(NodeType.EQUIPMENT, r.equipment_id)
            add_node(ekey, NodeType.EQUIPMENT, eq.name if eq else f"설비 {r.equipment_id}", "설비",
                     process_step_id=eq.process_step_id if eq else None, at=r.started_at.isoformat())
            add_edge(skey, ekey, "belongs")
            add_edge(ekey, lkey, "process", run_id=r.id, at=r.started_at.isoformat())
            anchor = ekey
        else:
            add_edge(skey, lkey, "process", run_id=r.id, at=r.started_at.isoformat())
            anchor = skey
        for cid, value in r.conditions.items():
            d = g.conditions.get(cid)
            ckey = node_key(NodeType.CONDITION, cid)
            add_node(ckey, NodeType.CONDITION, d.name if d else f"조건 {cid}", "조건",
                     out_of_range=False)
            if cid in r.out_of_range:
                nodes[ckey]["out_of_range"] = True
            add_edge(ckey, anchor, "condition")
    return {
        "root": node_key(NodeType.LOT, g.root_lot_id),
        "direction": g.direction,
        "depth": g.depth,
        "truncated": g.truncated,
        "nodes": list(nodes.values()),
        "edges": list(edges.values()),
    }


# ---------------------------------------------------------------- 정방향

def seed_lots_for_node(
    db: Session, key: str, start: datetime | None, end: datetime | None
) -> tuple[NodeType, int, set[int]]:
    """정방향 추적 시작 LOT 집합. 설비/공정/조건은 기간 안 run 의 LOT."""
    try:
        ntype, nid = parse_node_key(key)
    except ValueError as exc:
        raise AppError(422, str(exc)) from exc
    if ntype == NodeType.LOT:
        if db.get(Lot, nid) is None:
            raise not_found("LOT")
        return ntype, nid, {nid}
    if start is None or end is None:
        raise AppError(422, "설비/공정/조건 노드는 기간(from, to)이 필요합니다.")
    stmt = select(ProcessRun.lot_id).where(ProcessRun.started_at >= start, ProcessRun.started_at <= end)
    if ntype == NodeType.EQUIPMENT:
        if db.get(Equipment, nid) is None:
            raise not_found("설비")
        stmt = stmt.where(ProcessRun.equipment_id == nid)
    elif ntype == NodeType.STEP:
        if db.get(ProcessStep, nid) is None:
            raise not_found("공정 단계")
        stmt = stmt.where(ProcessRun.process_step_id == nid)
    else:
        d = db.get(ConditionDefinition, nid)
        if d is None:
            raise not_found("작업 조건 항목")
        conds = []
        lo, hi, _ = d.effective_range()
        if lo is not None:
            conds.append(ConditionValue.value < lo)
        if hi is not None:
            conds.append(ConditionValue.value > hi)
        if not conds:
            return ntype, nid, set()
        stmt = (
            select(ProcessRun.lot_id)
            .join(ConditionValue, ConditionValue.process_run_id == ProcessRun.id)
            .where(
                ProcessRun.started_at >= start, ProcessRun.started_at <= end,
                and_(ConditionValue.condition_definition_id == nid, or_(*conds)),
            )
        )
    return ntype, nid, set(db.scalars(stmt))


def downstream_lots(db: Session, seeds: set[int], depth: int, max_nodes: int | None = None) -> tuple[dict[int, int], bool]:
    if not seeds:
        return {}, False
    return gs.traverse(db, list(seeds), "down", depth, max_nodes)


def default_window(db: Session, at: datetime) -> tuple[datetime, datetime]:
    days = get_setting(db, "cause_scoring")["downstream_window_days"]
    return at - timedelta(days=days), at + timedelta(days=days)


def downstream_payload(
    db: Session, key: str, start: datetime | None, end: datetime | None, depth: int | None
) -> dict:
    trace = get_setting(db, "trace")
    depth = min(depth or get_setting(db, "cause_scoring")["max_trace_depth"], trace["max_depth"])
    ntype, nid, seeds = seed_lots_for_node(db, key, start, end)
    reached, truncated = downstream_lots(db, seeds, depth, trace["max_nodes"])
    lots = db.execute(
        select(Lot, ItemType).join(ItemType, ItemType.id == Lot.item_type_id)
        .where(Lot.id.in_(list(reached) or [-1]))
        .order_by(Lot.created_at)
    ).all()
    edges = gs.edges_within(db, set(reached))
    affected = [
        {"id": l.id, "lot_no": l.lot_no, "item_type": it.name, "category": it.category.value,
         "status": l.status.value, "created_at": l.created_at, "depth": reached[l.id],
         "is_seed": l.id in seeds}
        for l, it in lots
    ]
    return {
        "node": key,
        "node_type": ntype.value,
        "window": {"from": start, "to": end} if ntype != NodeType.LOT else None,
        "depth": depth,
        "truncated": truncated,
        "seed_count": len(seeds),
        "lots": affected,
        "finished_count": sum(1 for a in affected if a["category"] == ItemCategory.FINISHED.value),
        "edges": [{"source": node_key(NodeType.LOT, e.parent_lot_id),
                   "target": node_key(NodeType.LOT, e.child_lot_id), "ratio": e.input_ratio} for e in edges],
    }
