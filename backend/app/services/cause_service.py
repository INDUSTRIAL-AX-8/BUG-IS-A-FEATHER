"""원인 후보 계산: 역방향 계보 그래프의 각 노드에 요소 (a)~(f) 점수를 매긴다.

계산 기준(설명 가능해야 하므로 단순하게 유지):
  - 분석 기간: 불량 LOT 생성 시각 ± analysis_window_days
  - 모집단: 분석 기간 안의 '완제품' 분류 LOT (불량 LOT 자신 제외), 불량 = 취소되지 않은 판정 존재
  - 노출군:
      LOT  → 그 LOT 의 하위 완제품
      설비 → 추적 run 시각 ± downstream_window_days 안에 그 설비에서 처리된 LOT 의 하위 완제품
      공정 → 분석 기간 안에 그 공정을 거친 LOT 의 하위 완제품 (대부분 전체 → Lift≈1)
      조건 → 분석 기간 안에 그 조건이 허용 범위를 벗어난 run 의 LOT 하위 완제품
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.causes.scoring import (
    FACTOR_LABELS,
    FACTORS,
    FactorResult,
    combine,
    design_mismatch,
    history_score,
    lift_score,
    lift_stats,
    spc_score,
)
from app.models import (
    CandidateSnapshot,
    Characteristic,
    ConditionValue,
    DefectJudgment,
    ForecastResult,
    ForecastStatus,
    ItemCategory,
    ItemType,
    Lot,
    NodeType,
    ProcessRun,
    ProductDesign,
    RootCauseConfirmation,
    SpcViolation,
    node_key,
    parse_node_key,
)
from app.services import trace_service as ts
from app.services.lot_service import is_out_of_range
from app.services.settings_service import get_setting


@dataclass
class Context:
    db: Session
    g: ts.TraceGraph
    cfg: dict[str, Any]
    weights: dict[str, float]
    window: tuple[datetime, datetime]
    W: timedelta
    population: set[int]
    defective: set[int]
    defect_type_id: int | None
    as_of: datetime | None
    max_depth: int


def _population(db: Session, start: datetime, end: datetime, exclude: int) -> set[int]:
    stmt = (
        select(Lot.id).join(ItemType, ItemType.id == Lot.item_type_id)
        .where(ItemType.category == ItemCategory.FINISHED, Lot.created_at >= start, Lot.created_at <= end)
    )
    return {i for i in db.scalars(stmt) if i != exclude}


def _defective(db: Session, population: set[int], as_of: datetime | None) -> set[int]:
    stmt = select(DefectJudgment.lot_id).where(DefectJudgment.is_cancelled.is_(False))
    if as_of is not None:
        stmt = stmt.where(DefectJudgment.judged_at <= as_of)
    return {i for i in db.scalars(stmt) if i in population}


def _descendants(ctx: Context, seeds: set[int], cache: dict) -> set[int]:
    key = frozenset(seeds)
    if key not in cache:
        reached, _ = ts.downstream_lots(ctx.db, seeds, ctx.max_depth)
        cache[key] = set(reached)
    return cache[key]


def _runs_lots(ctx: Context, *, equipment_id=None, step_id=None, start=None, end=None) -> set[int]:
    stmt = select(ProcessRun.lot_id).where(ProcessRun.started_at >= start, ProcessRun.started_at <= end)
    if equipment_id is not None:
        stmt = stmt.where(ProcessRun.equipment_id == equipment_id)
    if step_id is not None:
        stmt = stmt.where(ProcessRun.process_step_id == step_id)
    return set(ctx.db.scalars(stmt))


def _cond_out_lots(ctx: Context, cond_id: int) -> set[int]:
    d = ctx.g.conditions.get(cond_id)
    if d is None or (d.min_value is None and d.max_value is None):
        return set()
    rows = ctx.db.execute(
        select(ProcessRun.lot_id, ConditionValue.value)
        .join(ConditionValue, ConditionValue.process_run_id == ProcessRun.id)
        .where(
            ConditionValue.condition_definition_id == cond_id,
            ProcessRun.started_at >= ctx.window[0], ProcessRun.started_at <= ctx.window[1],
        )
    )
    return {lot for lot, v in rows if is_out_of_range(v, d.min_value, d.max_value)}


def _eq_window(ctx: Context, runs: list[ts.RunInfo]) -> tuple[datetime, datetime]:
    times = [r.started_at for r in runs]
    return min(times) - ctx.W, max(times) + ctx.W


def analyze(
    db: Session,
    lot: Lot,
    depth: int | None = None,
    as_of: datetime | None = None,
    defect_type_id: int | None = None,
) -> dict[str, Any]:
    cfg = get_setting(db, "cause_scoring")
    weights = get_setting(db, "cause_weights")
    g = ts.build_upstream(db, lot, depth or cfg["max_trace_depth"])
    window = (lot.created_at - timedelta(days=cfg["analysis_window_days"]),
              lot.created_at + timedelta(days=cfg["analysis_window_days"]))
    if defect_type_id is None:
        defect_type_id = db.scalar(
            select(DefectJudgment.defect_type_id)
            .where(DefectJudgment.lot_id == lot.id, DefectJudgment.is_cancelled.is_(False))
            .order_by(DefectJudgment.judged_at.desc()).limit(1)
        )
    population = _population(db, *window, exclude=lot.id)
    ctx = Context(db, g, cfg, weights, window, timedelta(days=cfg["downstream_window_days"]),
                  population, _defective(db, population, as_of), defect_type_id, as_of,
                  cfg["max_trace_depth"])

    by_node = g.runs_by_node()
    graph = ts.graph_payload(g)
    keys = [n["id"] for n in graph["nodes"] if n["id"] != graph["root"]]

    violations = _load_violations(ctx)
    risks = _load_risks(ctx)
    design_issues = _design_issues(ctx)
    history = _load_history(ctx)
    desc_cache: dict = {}

    candidates = []
    for key in keys:
        ntype, nid = parse_node_key(key)
        runs = by_node.get(key, [])
        seeds = _exposure_seeds(ctx, ntype, nid, runs)
        if ntype == NodeType.CONDITION and not any(nid in r.out_of_range for r in runs):
            # 이 불량의 경로에서 이탈이 없었다면 다른 시점의 이탈과 연관 지을 근거가 없다
            lift = FactorResult(0.0, {"exposed_n": 0}, "이 불량의 추적 경로에서는 허용 범위 이탈 없음")
        else:
            lift = _lift_factor(ctx, seeds, desc_cache)
        factors = {
            "spc": _spc_factor(ctx, ntype, nid, runs, seeds, violations),
            "lift": lift,
            "arima": _arima_factor(ctx, ntype, nid, risks),
            "condition": _condition_factor(ctx, ntype, nid, runs),
            "design": _design_factor(design_issues.get(key, [])),
            "history": _history_factor(ctx, ntype, nid, history),
        }
        total, contrib = combine(factors, weights)
        node = next(n for n in graph["nodes"] if n["id"] == key)
        candidates.append({
            "node": key, "type": ntype.value, "label": node["label"], "sub": node.get("sub", ""),
            "score": round(total, 6),
            "contributions": {k: round(v, 6) for k, v in contrib.items()},
            "factors": {k: {"label": FACTOR_LABELS[k], "score": round(f.score, 6), "raw": f.raw, "note": f.note}
                        for k, f in factors.items()},
        })

    candidates.sort(key=lambda c: (-c["score"], c["type"] != "LOT", c["label"]))
    for i, c in enumerate(candidates, 1):
        c["rank"] = i
    rank_map = {c["node"]: {"score": c["score"], "rank": c["rank"]} for c in candidates}
    for n in graph["nodes"]:
        if n["id"] in rank_map:
            n.update(rank_map[n["id"]])
    return {
        "lot": {"id": lot.id, "lot_no": lot.lot_no, "status": lot.status.value},
        "defect_type_id": defect_type_id,
        "window": {"from": window[0], "to": window[1]},
        "population": {"finished_lots": len(population), "defective": len(ctx.defective)},
        "weights": weights,
        "factor_labels": FACTOR_LABELS,
        "candidates": candidates,
        "graph": graph,
    }


# ------------------------------------------------------------------ 요소별

def _exposure_seeds(ctx: Context, ntype: NodeType, nid: int, runs: list[ts.RunInfo]) -> set[int]:
    if ntype == NodeType.LOT:
        return {nid}
    if ntype == NodeType.EQUIPMENT:
        if not runs:
            return set()
        s, e = _eq_window(ctx, runs)
        return _runs_lots(ctx, equipment_id=nid, start=s, end=e)
    if ntype == NodeType.STEP:
        return _runs_lots(ctx, step_id=nid, start=ctx.window[0], end=ctx.window[1])
    return _cond_out_lots(ctx, nid)


def _lift_factor(ctx: Context, seeds: set[int], cache: dict) -> FactorResult:
    exposed = (_descendants(ctx, seeds, cache) | seeds) & ctx.population if seeds else set()
    other = ctx.population - exposed
    st = lift_stats(
        len(exposed & ctx.defective), len(exposed), len(other & ctx.defective), len(other),
        ctx.cfg["beta_prior_alpha"], ctx.cfg["beta_prior_beta"],
    )
    raw = {
        "exposed_n": st.exposed_n, "exposed_defects": st.exposed_defects,
        "other_n": st.other_n, "other_defects": st.other_defects,
        "rate_exposed": st.rate_exposed, "rate_other": st.rate_other,
        "lift": st.raw_lift, "smoothed_lift": st.smoothed_lift, "p_value": st.p_value,
    }
    if st.exposed_n == 0:
        note = "이 노드를 거친 완제품이 분석 기간에 없음"
    else:
        note = (f"거친 제품 {st.exposed_defects}/{st.exposed_n} 불량 vs 거치지 않은 제품 "
                f"{st.other_defects}/{st.other_n}" + (f", p={st.p_value:.3g}" if st.p_value is not None else ""))
    return FactorResult(lift_score(st, ctx.cfg["lift_saturation"]), raw, note)


def _load_violations(ctx: Context) -> list[tuple[int, int, set[int], datetime]]:
    start, end = ctx.window[0] - ctx.W, ctx.window[1] + ctx.W
    stmt = select(SpcViolation.characteristic_id, SpcViolation.rule_no, SpcViolation.lot_ids,
                  SpcViolation.occurred_at).where(SpcViolation.occurred_at >= start, SpcViolation.occurred_at <= end)
    return [(c, r, set(l or []), t) for c, r, l, t in ctx.db.execute(stmt)]


def _spc_factor(ctx, ntype, nid, runs, seeds, violations) -> FactorResult:
    if ntype == NodeType.CONDITION:
        return FactorResult(0.0, {"applicable": False}, "조건 항목은 SPC 대상 아님")
    if ntype == NodeType.STEP:
        step_chars = {c.id for c in ctx.db.scalars(select(Characteristic).where(Characteristic.process_step_id == nid))}
        if not runs:
            hits = []
        else:
            s, e = _eq_window(ctx, runs)
            hits = [(c, r) for c, r, _, t in violations if c in step_chars and s <= t <= e]
    else:
        targets = {nid} if ntype == NodeType.LOT else seeds
        hits = [(c, r) for c, r, lots, _ in violations if lots & targets]
    rules = sorted({r for _, r in hits})
    raw = {"violations": len(hits), "rules": rules, "characteristics": len({c for c, _ in hits})}
    note = f"위반 {len(hits)}건, 룰 {', '.join('R%d' % r for r in rules)}" if hits else "관련 위반 없음"
    return FactorResult(spc_score(len(rules), ctx.cfg["spc_rule_saturation"]), raw, note)


def _load_risks(ctx: Context) -> dict[int, float]:
    """특성별 최신 ARIMA 위험 점수 (as_of 이전)."""
    stmt = select(ForecastResult.characteristic_id, ForecastResult.risk_score, ForecastResult.run_at).where(
        ForecastResult.status == ForecastStatus.OK
    )
    if ctx.as_of is not None:
        stmt = stmt.where(ForecastResult.run_at <= ctx.as_of)
    latest: dict[int, tuple[datetime, float]] = {}
    for cid, risk, at in ctx.db.execute(stmt):
        if risk is not None and (cid not in latest or at > latest[cid][0]):
            latest[cid] = (at, risk)
    return {cid: v for cid, (_, v) in latest.items()}


def _arima_factor(ctx, ntype, nid, risks) -> FactorResult:
    if ntype == NodeType.CONDITION or not risks:
        return FactorResult(0.0, {"risk": None}, "예측 결과 없음" if ntype != NodeType.CONDITION else "해당 없음")
    if ntype == NodeType.LOT:
        lot = ctx.g.lots.get(nid)
        stmt = select(Characteristic.id, Characteristic.name).where(Characteristic.item_type_id == (lot.item_type_id if lot else -1))
    elif ntype == NodeType.EQUIPMENT:
        eq = ctx.g.equipment.get(nid)
        stmt = select(Characteristic.id, Characteristic.name).where(
            Characteristic.process_step_id == (eq.process_step_id if eq and eq.process_step_id else -1))
    else:
        stmt = select(Characteristic.id, Characteristic.name).where(Characteristic.process_step_id == nid)
    best = None
    for cid, name in ctx.db.execute(stmt):
        if cid in risks and (best is None or risks[cid] > best[1]):
            best = (name, risks[cid])
    if best is None:
        return FactorResult(0.0, {"risk": None}, "관련 특성 예측 없음")
    return FactorResult(best[1], {"risk": best[1], "characteristic": best[0]},
                        f"'{best[0]}' 이탈 위험 {best[1]:.2f}")


def _condition_factor(ctx, ntype, nid, runs) -> FactorResult:
    if ntype == NodeType.CONDITION:
        relevant = [r for r in runs if nid in r.conditions]
        out = [r for r in relevant if nid in r.out_of_range]
        d = ctx.g.conditions.get(nid)
        values = [round(r.out_of_range[nid], 4) for r in out]
        raw = {"runs": len(relevant), "out_of_range": len(out), "values": values[:10],
               "range": [d.min_value, d.max_value] if d else None}
    else:
        relevant = runs
        out = [r for r in runs if r.out_of_range]
        names = sorted({ctx.g.conditions[c].name for r in out for c in r.out_of_range if c in ctx.g.conditions})
        raw = {"runs": len(relevant), "out_of_range": len(out), "conditions": names}
    if not relevant:
        return FactorResult(0.0, raw, "조건 측정 없음")
    frac = len(out) / len(relevant)
    note = f"추적 run {len(relevant)}건 중 {len(out)}건 허용 범위 이탈" if out else "이탈 없음"
    return FactorResult(frac, raw, note)


def _design_issues(ctx: Context) -> dict[str, list[str]]:
    """추적 범위 LOT 의 실제 투입/공정 vs 설계. {노드키: [설명...]}"""
    g = ctx.g
    designs: dict[int, ProductDesign] = {}
    for d in ctx.db.scalars(select(ProductDesign).where(ProductDesign.is_active.is_(True))
                            .order_by(ProductDesign.version)):
        designs[d.output_item_type_id] = d  # 최신 버전이 마지막에 덮어씀
    parents: dict[int, list] = defaultdict(list)
    for e in g.edges:
        parents[e.child_lot_id].append(e)
    runs_by_lot: dict[int, set[int]] = defaultdict(set)
    for r in g.runs:
        runs_by_lot[r.lot_id].add(r.step_id)

    issues: dict[str, list[str]] = defaultdict(list)
    for lot in g.lots.values():
        design = designs.get(lot.item_type_id)
        if design is None:
            continue
        lkey = node_key(NodeType.LOT, lot.id)
        edges = parents.get(lot.id, [])
        at_boundary = g.lot_depth.get(lot.id, 0) >= g.depth
        if edges and not at_boundary and design.components:
            weights_ = [e.input_ratio if e.input_ratio is not None else e.input_qty for e in edges]
            if any(w is None for w in weights_):
                weights_ = [1.0] * len(edges)
            total = sum(weights_) or 1.0
            actual: dict[int, float] = defaultdict(float)
            parent_by_type: dict[int, list[int]] = defaultdict(list)
            for e, w in zip(edges, weights_):
                p = g.lots.get(e.parent_lot_id)
                if p is None:
                    continue
                actual[p.item_type_id] += w / total
                parent_by_type[p.item_type_id].append(p.id)
            expected = {c.item_type_id: (c.ratio, c.tolerance) for c in design.components}
            for type_id, msg in design_mismatch(actual, expected, ctx.cfg["design_ratio_tolerance"]):
                it = g.item_types.get(type_id)
                text = f"{lot.lot_no}: {it.name if it else type_id} {msg}"
                issues[lkey].append(text)
                for pid in parent_by_type.get(type_id, []):
                    issues[node_key(NodeType.LOT, pid)].append(text)
        if runs_by_lot.get(lot.id) and design.steps:
            for st in design.steps:
                if st.process_step_id not in runs_by_lot[lot.id]:
                    step = g.steps.get(st.process_step_id)
                    issues[lkey].append(f"{lot.lot_no}: 설계 공정 '{step.name if step else st.process_step_id}' 처리 이력 없음")
    return issues


def _design_factor(items: list[str]) -> FactorResult:
    if not items:
        return FactorResult(0.0, {"issues": []}, "설계와 일치")
    return FactorResult(1.0, {"issues": items[:10]}, items[0] + (f" 외 {len(items) - 1}건" if len(items) > 1 else ""))


def _load_history(ctx: Context) -> dict[str, Any]:
    stmt = (
        select(RootCauseConfirmation.node_type, RootCauseConfirmation.node_id, RootCauseConfirmation.confidence,
               DefectJudgment.defect_type_id)
        .join(DefectJudgment, DefectJudgment.id == RootCauseConfirmation.judgment_id)
        .where(DefectJudgment.is_cancelled.is_(False))
    )
    if ctx.as_of is not None:
        stmt = stmt.where(RootCauseConfirmation.confirmed_at <= ctx.as_of)
    if ctx.defect_type_id is not None:
        stmt = stmt.where(DefectJudgment.defect_type_id == ctx.defect_type_id)
    by_node: dict[str, float] = defaultdict(float)
    lot_ids: dict[int, float] = defaultdict(float)
    for ntype, nid, conf, _ in ctx.db.execute(stmt):
        by_node[node_key(ntype, nid)] += conf
        if ntype == NodeType.LOT:
            lot_ids[nid] += conf
    by_type: dict[int, dict[int, float]] = defaultdict(dict)  # item_type → {lot_id: conf}
    if lot_ids:
        for lid, tid in ctx.db.execute(select(Lot.id, Lot.item_type_id).where(Lot.id.in_(list(lot_ids)))):
            by_type[tid][lid] = lot_ids[lid]
    return {"by_node": by_node, "by_type": by_type}


def _history_factor(ctx, ntype, nid, history) -> FactorResult:
    key = node_key(ntype, nid)
    same_node = history["by_node"].get(key, 0.0)
    same_type = 0.0
    if ntype == NodeType.LOT and nid in ctx.g.lots:
        tid = ctx.g.lots[nid].item_type_id
        same_type = sum(v for lid, v in history["by_type"].get(tid, {}).items() if lid != nid)
    score = history_score(same_node, same_type, ctx.cfg["history_type_weight"], ctx.cfg["history_saturation"])
    raw = {"same_node": round(same_node, 3), "same_item_type": round(same_type, 3)}
    if same_node or same_type:
        note = f"같은 노드 확정 {same_node:g}건" + (f", 같은 부품 종류 확정 {same_type:g}건" if same_type else "")
    else:
        note = "확정 이력 없음"
    return FactorResult(score, raw, note)


# ------------------------------------------------------------------ 스냅샷

def save_snapshot(db: Session, judgment: DefectJudgment, result: dict[str, Any], at: datetime) -> CandidateSnapshot:
    snap = CandidateSnapshot(
        judgment_id=judgment.id,
        computed_at=at,
        ranked=[
            {"node": c["node"], "rank": c["rank"], "score": c["score"], "label": c["label"],
             "factors": {k: c["factors"][k]["score"] for k in FACTORS}}
            for c in result["candidates"]
        ],
    )
    db.add(snap)
    return snap
