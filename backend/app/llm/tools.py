"""LLM 이 호출할 수 있는 고정 도구 함수. LLM 은 DB 를 직접 조회하거나 계산하지 않는다.

각 도구는 (결과 dict, 근거 refs) 를 돌려준다. refs 는 화면 링크로 쓰인다.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Callable

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import (
    Characteristic,
    DefectJudgment,
    DefectType,
    ItemType,
    Lot,
    NodeType,
    SpcViolation,
    node_key,
)
from app.services import cause_service, forecast_service, genealogy_service as gs, lot_service, trace_service as ts

Ref = dict[str, Any]
MAX_LIST = 30


def _lot_ref(lot_no: str) -> Ref:
    return {"kind": "lot", "id": lot_no, "label": lot_no, "link": f"/genealogy?lot={lot_no}"}


def _iso(v: datetime | None) -> str | None:
    return v.strftime("%Y-%m-%d %H:%M") if v else None


def _parse_dt(v: Any) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).strip().replace(" ", "T"))
    except ValueError as exc:
        raise AppError(422, f"날짜 형식이 올바르지 않습니다: {v} (예: 2026-03-02 또는 2026-03-02T08:00)") from exc


# ------------------------------------------------------------------ 도구

def get_lot(db: Session, lot_no: str) -> tuple[dict, list[Ref]]:
    lot = gs.lot_by_no(db, str(lot_no).strip())
    detail = lot_service.lot_detail(db, lot)
    runs = lot_service.runs_for_lots(db, [lot.id])
    judgments = db.execute(
        select(DefectJudgment, DefectType).join(DefectType, DefectType.id == DefectJudgment.defect_type_id)
        .where(DefectJudgment.lot_id == lot.id, DefectJudgment.is_cancelled.is_(False))
    ).all()
    refs = [_lot_ref(lot.lot_no)] + [
        {"kind": "judgment", "id": j.id, "label": f"판정 #{j.id}", "link": f"/genealogy?lot={lot.lot_no}"}
        for j, _ in judgments
    ]
    return {
        "lot_no": lot.lot_no, "item_type": detail.item_type_name, "status": lot.status.value,
        "created_at": _iso(lot.created_at), "parents": detail.parent_count, "children": detail.child_count,
        "process_runs": [
            {"step": r.process_step_name, "equipment": r.equipment_name, "started_at": _iso(r.started_at),
             "out_of_range_conditions": [f"{c.name}={c.value:g}{c.unit or ''} (허용 {c.min_value}~{c.max_value})"
                                         for c in r.conditions if c.out_of_range]}
            for r in runs
        ],
        "judgments": [{"judgment_id": j.id, "defect_type": f"{t.code} {t.name}", "judged_at": _iso(j.judged_at),
                       "memo": j.memo} for j, t in judgments],
    }, refs


def trace_upstream(db: Session, lot_no: str, depth: int = 4) -> tuple[dict, list[Ref]]:
    lot = gs.lot_by_no(db, str(lot_no).strip())
    g = ts.build_upstream(db, lot, max(1, min(int(depth or 4), 10)))
    lots = sorted(g.lots.values(), key=lambda l: (g.lot_depth.get(l.id, 0), l.lot_no))
    eq = sorted({g.equipment[r.equipment_id].name for r in g.runs if r.equipment_id in g.equipment})
    out_conds = sorted({f"{g.conditions[c].name}" for r in g.runs for c in r.out_of_range if c in g.conditions})
    refs = [_lot_ref(l.lot_no) for l in lots[:MAX_LIST]]
    return {
        "root": lot.lot_no,
        "upstream_lots": [{"lot_no": l.lot_no, "item_type": g.item_types[l.item_type_id].name,
                           "depth": g.lot_depth.get(l.id), "status": l.status.value} for l in lots[:MAX_LIST]],
        "total_lots": len(lots), "equipment": eq,
        "steps": sorted({g.steps[r.step_id].name for r in g.runs if r.step_id in g.steps}),
        "out_of_range_conditions": out_conds, "truncated": g.truncated or len(lots) > MAX_LIST,
    }, refs


def trace_downstream(db: Session, node_id: str, depth: int = 6, at: str | None = None) -> tuple[dict, list[Ref]]:
    node_id = str(node_id).strip()
    if ":" not in node_id:  # LOT 번호로 들어온 경우
        node_id = node_key(NodeType.LOT, gs.lot_by_no(db, node_id).id)
    start = end = None
    if not node_id.startswith("lot:"):
        ref_time = _parse_dt(at) or datetime.now()
        start, end = ts.default_window(db, ref_time)
    d = ts.downstream_payload(db, node_id, start, end, max(1, min(int(depth or 6), 12)))
    finished = [l for l in d["lots"] if l["category"] == "FINISHED"]
    defect = [l for l in finished if l["status"] == "DEFECT"]
    refs = [_lot_ref(l["lot_no"]) for l in defect[:MAX_LIST]]
    return {
        "node": node_id, "window": {"from": _iso(start), "to": _iso(end)} if start else None,
        "affected_lots": len(d["lots"]), "affected_finished": len(finished), "already_defect": len(defect),
        "status_counts": {s: sum(1 for l in finished if l["status"] == s) for s in {l["status"] for l in finished}},
        "sample_finished": [l["lot_no"] for l in finished[:MAX_LIST]],
        "truncated": d["truncated"],
    }, refs


def get_root_cause_candidates(db: Session, lot_no: str) -> tuple[dict, list[Ref]]:
    lot = gs.lot_by_no(db, str(lot_no).strip())
    r = cause_service.analyze(db, lot)
    top = r["candidates"][:5]
    refs = [_lot_ref(lot.lot_no)] + [_lot_ref(c["label"]) for c in top if c["type"] == "LOT"]
    return {
        "lot_no": lot.lot_no,
        "population": r["population"],
        "candidates": [
            {"rank": c["rank"], "type": c["type"], "name": c["label"], "score": round(c["score"], 3),
             "evidence": {f["label"]: f["note"] for k, f in c["factors"].items() if f["score"] > 0},
             "common_path_note": (c.get("specificity") or {}).get("note") or None}
            for c in top
        ],
        "note": "점수는 요소별 근거의 가중 평균이며 확정 원인이 아닙니다.",
    }, refs


def get_spc_violations(db: Session, characteristic: str | int | None = None, from_: str | None = None,
                       to: str | None = None) -> tuple[dict, list[Ref]]:
    """넬슨룰 위반 이력. 같은 이상에 함께 걸린 룰은 사건 1건으로 묶어 돌려준다."""
    from app.services import spc_service

    chars = select(Characteristic)
    if characteristic not in (None, ""):
        text = str(characteristic).strip()
        chars = chars.where(Characteristic.id == int(text)) if text.isdigit() else chars.where(Characteristic.name.contains(text))
    names = {c.id: c.name for c in db.scalars(chars)}
    s, e = _parse_dt(from_), _parse_dt(to)
    events = [ev for cid in names for ev in spc_service.load_events(db, cid, s, e)]
    events.sort(key=lambda ev: ev.occurred_at, reverse=True)
    total = sum(len(ev.members) for ev in events)
    shown = events[:MAX_LIST]
    lot_ids = {lid for ev in shown for lid in ev.lot_ids[:3]}
    lot_nos = dict(db.execute(select(Lot.id, Lot.lot_no).where(Lot.id.in_(lot_ids or {-1}))).all())
    direction = {1: "상승", -1: "하락", 0: "양쪽"}
    refs = [{"kind": "violation", "id": ev.id, "label": f"위반 #{ev.id}", "link": f"/spc?char={ev.characteristic_id}"}
            for ev in shown]
    return {
        "total": total, "total_events": len(events), "shown": len(shown),
        "note": "같은 이상에 함께 걸린 넬슨룰 위반은 사건 1건으로 묶었다. 사건은 가장 작은 위반 번호로 부른다.",
        "events": [
            {"event": f"위반 #{ev.id}", "violation_ids": ev.violation_ids, "characteristic": names[ev.characteristic_id],
             "rules": [f"R{r}" for r in ev.rule_nos], "direction": direction[ev.direction],
             "occurred_at": _iso(ev.occurred_at), "acknowledged": ev.acknowledged,
             "lots": [lot_nos.get(i) for i in ev.lot_ids[:3] if i in lot_nos]}
            for ev in shown
        ],
    }, refs


def get_watchlist(db: Session) -> tuple[dict, list[Ref]]:
    items = forecast_service.watchlist(db)[:10]
    refs = [{"kind": "characteristic", "id": i["characteristic_id"], "label": i["characteristic"],
             "link": f"/spc?char={i['characteristic_id']}"} for i in items if i["status"] == "OK"]
    return {
        "items": [
            {"part_or_step": i["item_type"] or i["process_step"], "characteristic": i["characteristic"],
             "status": i["status"], "risk_score": None if i["risk_score"] is None else round(i["risk_score"], 3),
             "expected_breach_at": _iso(i["expected_breach_at"]), "trend": i["trend"], "message": i["message"],
             "forecast_run_at": _iso(i["run_at"])}
            for i in items
        ],
        "note": "통계적 예측이며 확정 판단이 아닙니다.",
    }, refs


_PERIOD = re.compile(r"^\s*(\d+)\s*([dw일주])\s*$")


def search_defect_history(db: Session, defect_type: str | None = None, item_type: str | None = None,
                          period: str | None = None) -> tuple[dict, list[Ref]]:
    stmt = (select(DefectJudgment, Lot, DefectType, ItemType)
            .join(Lot, Lot.id == DefectJudgment.lot_id)
            .join(DefectType, DefectType.id == DefectJudgment.defect_type_id)
            .join(ItemType, ItemType.id == Lot.item_type_id)
            .where(DefectJudgment.is_cancelled.is_(False)))
    if defect_type:
        t = str(defect_type).strip()
        stmt = stmt.where(or_(DefectType.code.contains(t.upper()), DefectType.name.contains(t)))
    if item_type:
        stmt = stmt.where(ItemType.name.contains(str(item_type).strip()))
    anchor = db.scalar(select(func.max(DefectJudgment.judged_at)))
    period_info = None
    if period:
        p = str(period).strip()
        m = _PERIOD.match(p)
        if m and anchor:
            days = int(m.group(1)) * (7 if m.group(2) in ("w", "주") else 1)
            start = anchor - timedelta(days=days)
            stmt = stmt.where(DefectJudgment.judged_at >= start)
            period_info = {"from": _iso(start), "to": _iso(anchor), "anchor": "데이터의 최신 판정 시각 기준"}
        elif "~" in p:
            a, b = (x.strip() for x in p.split("~", 1))
            s, e = _parse_dt(a), _parse_dt(b)
            if e and len(b) <= 10:
                e = e + timedelta(days=1)
            stmt = stmt.where(DefectJudgment.judged_at >= s, DefectJudgment.judged_at < e)
            period_info = {"from": a, "to": b}
        else:
            raise AppError(422, "period 는 '7d', '2w' 또는 '2026-03-01~2026-03-07' 형식이어야 합니다.")
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.execute(stmt.order_by(DefectJudgment.judged_at.desc()).limit(MAX_LIST)).all()
    by_type: dict[str, int] = {}
    for _, _, t, _ in db.execute(stmt):
        by_type[f"{t.code} {t.name}"] = by_type.get(f"{t.code} {t.name}", 0) + 1
    refs = [{"kind": "judgment", "id": j.id, "label": f"판정 #{j.id}", "link": f"/genealogy?lot={l.lot_no}"}
            for j, l, _, _ in rows]
    return {
        "total": total, "period": period_info, "by_defect_type": by_type,
        "recent": [{"judgment_id": j.id, "lot_no": l.lot_no, "item_type": it.name, "defect_type": f"{t.code} {t.name}",
                    "judged_at": _iso(j.judged_at)} for j, l, t, it in rows],
    }, refs


def get_process_drivers(db: Session, equipment: str | None = None) -> tuple[dict, list[Ref]]:
    """설비 기록 전체의 요인 분석 결론 (주요 원인 후보, 교란 판정, 근거, 권고)."""
    from app.models import Equipment
    from app.services.driver_service import analyze_equipment

    stmt = select(Equipment)
    if equipment:
        stmt = stmt.where(Equipment.name.contains(str(equipment).strip()))
    eqs = db.scalars(stmt).all()
    if not eqs:
        raise AppError(404, f"설비를 찾을 수 없습니다: {equipment}")
    eq = eqs[0]
    r = analyze_equipment(db, eq.id)
    refs = [{"kind": "analysis", "id": eq.id, "label": f"{eq.name} 요인 분석", "link": f"/drivers?equipment={eq.id}"}]
    return {
        "equipment": eq.name, "status": r["status"], "verdict": r["verdict"],
        "factors": [{"name": f["name"], "status": f.get("status"), "p": round(f["p"], 4)} for f in r["factors"]],
        "learned_thresholds": r.get("thresholds"), "caveats": r["caveats"],
    }, refs


# ------------------------------------------------------------------ 스키마

def _fn(name: str, desc: str, props: dict[str, Any], required: list[str]) -> dict:
    return {"type": "function", "function": {"name": name, "description": desc,
                                             "parameters": {"type": "object", "properties": props, "required": required}}}


TOOL_SCHEMAS = [
    _fn("get_lot", "LOT 하나의 기본 정보, 공정 처리 이력(설비, 허용 범위 이탈 조건), 불량 판정을 조회한다.",
        {"lot_no": {"type": "string", "description": "LOT 번호"}}, ["lot_no"]),
    _fn("trace_upstream", "불량 LOT 에서 상위 계보(원자재·배치 LOT, 공정, 설비, 이탈 조건)를 거슬러 조회한다.",
        {"lot_no": {"type": "string"}, "depth": {"type": "integer", "description": "추적 깊이 1~10, 기본 4"}}, ["lot_no"]),
    _fn("trace_downstream", "원인 후보 노드를 공유하는 하위 LOT(잠재 불량 범위)를 조회한다.",
        {"node_id": {"type": "string", "description": "노드 ID(lot:12, eq:3, step:2, cond:5) 또는 LOT 번호"},
         "depth": {"type": "integer"}, "at": {"type": "string", "description": "설비/공정/조건 노드의 기준 시각 (ISO)"}},
        ["node_id"]),
    _fn("get_root_cause_candidates", "불량 LOT 의 원인 후보 상위 5개와 요소별 근거를 조회한다.",
        {"lot_no": {"type": "string"}}, ["lot_no"]),
    _fn("get_spc_violations", "측정 특성의 넬슨룰 위반 이력을 조회한다. 같은 이상에 함께 걸린 룰은 사건 1건으로 묶여 있다.",
        {"characteristic": {"type": "string", "description": "특성 이름 일부 또는 ID"},
         "from": {"type": "string", "description": "시작 (ISO 날짜)"}, "to": {"type": "string", "description": "끝 (ISO 날짜)"}},
        []),
    _fn("get_watchlist", "ARIMA 예측으로 관리한계·규격 이탈 위험이 큰 주의 부품 순위를 조회한다.", {}, []),
    _fn("search_defect_history", "불량 판정 이력을 불량 유형, 부품 종류, 기간으로 검색한다.",
        {"defect_type": {"type": "string", "description": "불량 유형 코드나 이름 일부"},
         "item_type": {"type": "string", "description": "부품 종류 이름 일부"},
         "period": {"type": "string", "description": "'7d', '2w' 또는 '2026-03-01~2026-03-07'"}}, []),
]

TOOL_SCHEMAS.append(_fn(
    "get_process_drivers",
    "설비 기록 전체에서 불량을 늘린 요인(조건·작업자)을 판정한 요인 분석 결론을 조회한다. '왜 불량이 늘었나', '작업자 탓인가' 같은 질문에 쓴다.",
    {"equipment": {"type": "string", "description": "설비 이름 일부 (예: 사출기 3호)"}}, []))

TOOLS: dict[str, Callable[..., tuple[dict, list[Ref]]]] = {
    "get_process_drivers": get_process_drivers,
    "get_lot": get_lot,
    "trace_upstream": trace_upstream,
    "trace_downstream": trace_downstream,
    "get_root_cause_candidates": get_root_cause_candidates,
    "get_spc_violations": lambda db, characteristic=None, to=None, **kw: get_spc_violations(
        db, characteristic, kw.get("from") or kw.get("from_"), to),
    "get_watchlist": get_watchlist,
    "search_defect_history": search_defect_history,
}


def call_tool(db: Session, name: str, args: dict[str, Any]) -> tuple[dict, list[Ref]]:
    if name not in TOOLS:
        raise AppError(422, f"알 수 없는 도구입니다: {name}")
    if not isinstance(args, dict):
        args = {}
    allowed = {p for s in TOOL_SCHEMAS if s["function"]["name"] == name
               for p in s["function"]["parameters"]["properties"]}
    clean = {k: v for k, v in args.items() if k in allowed}
    return TOOLS[name](db, **clean)

