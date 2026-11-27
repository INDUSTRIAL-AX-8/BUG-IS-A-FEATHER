"""요인 분석 조립: DB 의 범용 모델(공정 run, 조건값, 작업자, LOT 수량, 불량 판정, 설비 이벤트) → 분석 행 → 결론.

한 행 = 대상 설비(또는 공정)의 run 하나.
  생산 수 n / 불량 수 k: run 의 LOT 과 그 하위 계보 중 '완제품' LOT 들의 합
    - LOT 수량(qty)이 있으면 수량, 없으면 1
    - 불량 판정에 불량 수량(defect_qty)이 있으면 그 수량, 없으면 LOT 전체
  연속 요인: run 의 작업 조건값 (조건 항목 이름별)
  범주 요인: 작업자 (기록이 있을 때)
  최다 불량 유형: 판정 유형 중 가장 많은 것
집계 기록(사출기 1시간 = 1행)과 LOT 단위 기록(KAMP shot → 캐비티 제품)이 같은 방식으로 분석된다.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.causes.drivers import Event, Row, analyze
from app.errors import AppError, not_found
from app.models import (
    ConditionDefinition,
    ConditionValue,
    DefectJudgment,
    DefectType,
    Equipment,
    EquipmentEvent,
    ItemCategory,
    ItemType,
    Lot,
    ProcessRun,
    ProcessStep,
    User,
)
from app.services import audit
from app.services.genealogy_service import children_of
from app.services.settings_service import get_setting

OPERATOR = "작업자"
MAX_DEPTH = 10
CONFIDENT = ("높음", "보통")


def _descendant_finished(db: Session, seeds: set[int], finished: set[int]) -> dict[int, set[int]]:
    """seed LOT → 자신과 하위 계보의 완제품 LOT 집합 (단계별 배치 조회)."""
    owner: dict[int, set[int]] = {s: {s} for s in seeds}
    frontier = set(seeds)
    child_fn = children_of(db)
    for _ in range(MAX_DEPTH):
        if not frontier:
            break
        kids = child_fn(frontier)
        nxt = set()
        for parent, children in kids.items():
            for c in children:
                before = len(owner.setdefault(c, set()))
                owner[c] |= owner[parent]
                if len(owner[c]) != before:
                    nxt.add(c)
        frontier = nxt
    result: dict[int, set[int]] = defaultdict(set)
    for lot, origins in owner.items():
        if lot in finished:
            for o in origins:
                result[o].add(lot)
    return result


def build_rows(db: Session, equipment_id: int | None = None, process_step_id: int | None = None,
               start: datetime | None = None, end: datetime | None = None) -> dict[str, Any]:
    if equipment_id is None and process_step_id is None:
        raise AppError(422, "설비 또는 공정 단계를 지정하세요.")
    stmt = select(ProcessRun)
    if equipment_id is not None:
        if db.get(Equipment, equipment_id) is None:
            raise not_found("설비")
        stmt = stmt.where(ProcessRun.equipment_id == equipment_id)
    if process_step_id is not None:
        stmt = stmt.where(ProcessRun.process_step_id == process_step_id)
    if start:
        stmt = stmt.where(ProcessRun.started_at >= start)
    if end:
        stmt = stmt.where(ProcessRun.started_at <= end)
    runs = db.scalars(stmt.order_by(ProcessRun.started_at)).all()
    if not runs:
        return {"rows": [], "value_kinds": {}, "units": {}, "events": [], "top_semantics": False}

    seed_lots = {r.lot_id for r in runs}
    finished = set(db.scalars(select(Lot.id).join(ItemType, ItemType.id == Lot.item_type_id)
                              .where(ItemType.category == ItemCategory.FINISHED)))
    desc = _descendant_finished(db, seed_lots, finished)
    all_finished = {l for s in desc.values() for l in s}
    qty = {}
    for i in range(0, len(all_finished), 500):
        chunk = list(all_finished)[i:i + 500]
        qty.update(dict(db.execute(select(Lot.id, Lot.qty).where(Lot.id.in_(chunk))).all()))
    judg: dict[int, list[tuple[float | None, str]]] = defaultdict(list)
    top_semantics = False
    for i in range(0, len(all_finished), 500):
        chunk = list(all_finished)[i:i + 500]
        for lot_id, dq, tname, tdesc in db.execute(
            select(DefectJudgment.lot_id, DefectJudgment.defect_qty, DefectType.name, DefectType.description)
            .join(DefectType, DefectType.id == DefectJudgment.defect_type_id)
            .where(DefectJudgment.lot_id.in_(chunk), DefectJudgment.is_cancelled.is_(False))
        ):
            judg[lot_id].append((dq, tname))
            top_semantics = top_semantics or bool(tdesc and "최다" in tdesc)

    defs = {d.id: d for d in db.scalars(select(ConditionDefinition))}
    cvals: dict[int, dict[str, float]] = defaultdict(dict)
    run_ids = [r.id for r in runs]
    for i in range(0, len(run_ids), 500):
        for rid, did, v in db.execute(select(ConditionValue.process_run_id, ConditionValue.condition_definition_id,
                                             ConditionValue.value).where(ConditionValue.process_run_id.in_(run_ids[i:i + 500]))):
            if did in defs:
                cvals[rid][defs[did].name] = v
    lot_nos = {}
    for i in range(0, len(seed_lots), 500):
        chunk = list(seed_lots)[i:i + 500]
        lot_nos.update(dict(db.execute(select(Lot.id, Lot.lot_no).where(Lot.id.in_(chunk))).all()))

    # 판정 대기 중인 불량 의심이 있는 LOT 은 불량 수를 아직 모르므로 분석에서 뺀다 (0 으로 세면 신호가 묻힘)
    from app.models import DefectSuspect

    pending_lots: set[int] = set()
    all_lots = list(all_finished | seed_lots)
    for i in range(0, len(all_lots), 500):
        pending_lots.update(db.scalars(select(DefectSuspect.lot_id).where(
            DefectSuspect.lot_id.in_(all_lots[i:i + 500]), DefectSuspect.status == "PENDING")))
    excluded = 0
    has_operator = any(r.operator_id for r in runs)
    rows: list[Row] = []
    for r in runs:
        if r.lot_id in pending_lots or any(l in pending_lots for l in desc.get(r.lot_id, ())):
            excluded += 1
            continue
        n = k = 0.0
        types: Counter = Counter()
        for lot in desc.get(r.lot_id, ()):
            w = float(qty.get(lot) or 1.0)
            n += w
            for dq, tname in judg.get(lot, []):
                k += min(float(dq), w) if dq is not None else w
                types[tname] += float(dq) if dq is not None else 1
        if n <= 0:
            continue
        rows.append(Row(time=r.started_at, n=n, k=min(k, n), numeric=dict(cvals.get(r.id, {})),
                        categorical={OPERATOR: r.operator_id} if has_operator else {},
                        top_type=types.most_common(1)[0][0] if types else None, label=lot_nos.get(r.lot_id, "")))

    names = {n for row in rows for n in row.numeric}
    value_kinds = {d.name: d.value_kind for d in defs.values() if d.name in names}
    units = {d.name: d.unit for d in defs.values() if d.name in names}
    ev_stmt = select(EquipmentEvent)
    if equipment_id is not None:
        ev_stmt = ev_stmt.where(EquipmentEvent.equipment_id == equipment_id)
    if start:
        ev_stmt = ev_stmt.where(EquipmentEvent.started_at >= start)
    if end:
        ev_stmt = ev_stmt.where(EquipmentEvent.started_at <= end)
    events = [Event(e.started_at, e.ended_at, e.kind, e.note) for e in db.scalars(ev_stmt)]
    return {"rows": rows, "value_kinds": value_kinds, "units": units, "events": events, "top_semantics": top_semantics,
            "excluded_pending": excluded}


def analyze_equipment(db: Session, equipment_id: int | None = None, process_step_id: int | None = None,
                      start: datetime | None = None, end: datetime | None = None,
                      apply_learned: bool = False, user: User | None = None) -> dict[str, Any]:
    cfg = get_setting(db, "driver_analysis")
    data = build_rows(db, equipment_id, process_step_id, start, end)
    eq = db.get(Equipment, equipment_id) if equipment_id else None
    step = db.get(ProcessStep, process_step_id) if process_step_id else None
    notes = list(get_setting(db, "dataset_notes").get(eq.name, []) if eq else [])
    if data.get("excluded_pending"):
        notes.append(f"불량 판정 대기 중인 기록 {data['excluded_pending']}건은 불량 수를 아직 몰라 분석에서 뺐습니다 (불량 판정 후 반영).")
    result = analyze(data["rows"], value_kinds=data["value_kinds"], units=data["units"], events=data["events"],
                     top_type_semantics=data["top_semantics"], caveats=notes, cfg=cfg)
    result["target"] = {"equipment_id": equipment_id, "equipment": eq.name if eq else None,
                        "process_step_id": process_step_id, "process_step": step.name if step else None}
    result["learned"] = []
    if apply_learned and cfg["learn_ranges"]:
        result["learned"] = apply_learned_ranges(db, result, user)
    return result


def apply_learned_ranges(db: Session, result: dict[str, Any], user: User | None) -> list[dict]:
    """확신도 보통 이상이면 선택된 연속 요인(주요인·보조 요인)의 학습 범위를 기록한다.
    현장이 허용 범위를 정의한 조건 항목은 건드리지 않는다. 모든 기록은 감사 로그에 남는다."""
    if result.get("status") != "OK" or result["verdict"]["confidence"] not in CONFIDENT:
        return []
    out = []
    for th in result.get("thresholds", []):
        for d in db.scalars(select(ConditionDefinition).where(ConditionDefinition.name == th["factor"])):
            if d.min_value is not None or d.max_value is not None:
                continue
            before = audit.snapshot(d)
            d.learned_min, d.learned_max = th["learned_min"], th["learned_max"]
            d.learned_at = datetime.now()
            role = "주요인" if th is result.get("threshold") else "보조 요인"
            d.learned_note = (f"요인 분석 학습({role}): 불량률 {th['rate_bad'] * 100:.1f}% (나쁜 구간 {th['rows_bad']}행) vs "
                              f"{th['rate_good'] * 100:.1f}% ({th['rows_good']}행), 확신도 {result['verdict']['confidence']}")
            audit.record(db, user, "learn_range", "condition_definition", d.id, before=before, after=audit.snapshot(d))
            out.append({"condition_definition_id": d.id, "name": d.name, "learned_min": d.learned_min,
                        "learned_max": d.learned_max, "role": role})
    db.commit()
    return out


def analyze_all(db: Session) -> dict[str, str]:
    """설비별 요인 분석 + 학습 범위 갱신 (배치)."""
    out = {}
    for eq in db.scalars(select(Equipment).where(Equipment.is_active.is_(True))):
        try:
            r = analyze_equipment(db, eq.id, apply_learned=True)
            out[eq.name] = r["verdict"]["headline"]
        except AppError:
            continue
    return out
