"""생산 대시보드: 생산량·CAPA·가동률, 확정 불량(적재된 불량 데이터), 불량 유형·발견 단계, 원인 Top.

'불량'은 실무자가 확정해 적재한 불량 판정(DefectJudgment)만 센다. 검토 대기 의심은 따로 표시한다.
원인 Top 의 '초과 불량'은 그 조건이 허용(또는 학습) 범위를 벗어난 시간의 불량 수에서,
'모든 조건이 정상인 시간'의 불량률로 기대되는 불량 수를 뺀 값이다 (그 조건 이탈 때문에 늘었다고 볼 수 있는 양).
기준을 '그 조건만 정상인 시간'으로 잡으면 다른 조건 이탈 시간이 섞여 원인이 가려지므로 이렇게 한다.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.deps import viewer
from app.db import get_db
from app.models import (
    ConditionDefinition,
    ConditionValue,
    DefectJudgment,
    DefectSuspect,
    DefectType,
    Equipment,
    EquipmentEvent,
    Lot,
    ProcessRun,
    ProcessStep,
    RootCauseConfirmation,
)
from app.services.settings_service import get_setting

router = APIRouter(prefix="/production", tags=["생산 대시보드"])


def _main_equipment(db: Session) -> list[Equipment]:
    """생산 수를 세는 설비: 공정 흐름의 주 설비, 없으면 '수량이 있는 LOT 을 처음 만든' 설비."""
    lines = get_setting(db, "process_lines")
    names = [s["equipment"] for line in lines.values() for s in line if s.get("main")]
    if names:
        return list(db.scalars(select(Equipment).where(Equipment.name.in_(names))))
    ids = {i for (i,) in db.execute(select(ProcessRun.equipment_id).join(Lot, Lot.id == ProcessRun.lot_id)
                                    .where(Lot.qty > 1, ProcessRun.equipment_id.is_not(None)).distinct())}
    return list(db.scalars(select(Equipment).where(Equipment.id.in_(ids or {-1}))))


@router.get("/dashboard")
def dashboard(
    start: datetime | None = Query(default=None, alias="from"),
    end: datetime | None = Query(default=None, alias="to"),
    equipment_id: int | None = None,
    db: Session = Depends(get_db),
    _=Depends(viewer),
):
    return _compute(db, start, end, equipment_id)


def _bounds(db: Session, eq_ids: list[int]) -> tuple[datetime | None, datetime | None]:
    q = select(func.min(ProcessRun.started_at), func.max(ProcessRun.started_at)).where(ProcessRun.equipment_id.in_(eq_ids))
    lo, hi = db.execute(q).one()
    return lo, hi


@router.get("/range")
def data_range(db: Session = Depends(get_db), _=Depends(viewer)):
    """생산 기록이 있는 기간 (기간 선택의 기준일)."""
    mains = _main_equipment(db)
    lo, hi = _bounds(db, [e.id for e in mains]) if mains else (None, None)
    return {"from": lo, "to": hi}


@router.get("/overview")
def overview(
    start: datetime | None = Query(default=None, alias="from"),
    end: datetime | None = Query(default=None, alias="to"),
    equipment_id: int | None = None,
    db: Session = Depends(get_db),
    _=Depends(viewer),
):
    """경영 현황: 기간 KPI + 직전 같은 길이 기간 비교, 일/주 추이, 설비별, 원인 Top, 한 줄 요약."""
    cur = _compute(db, start, end, equipment_id)
    if cur.get("message"):
        return cur
    s, e = cur["period"]["from"], cur["period"]["to"]
    span = e - s
    prev = _compute(db, s - span - timedelta(seconds=1), s - timedelta(seconds=1), equipment_id) if span.total_seconds() > 0 else None
    pk = prev["kpi"] if prev and not prev.get("message") and prev["kpi"]["produced"] else None
    lo, hi = _bounds(db, [x["id"] for x in cur["equipment"]])
    cur["previous"] = {"period": prev["period"], "kpi": pk} if pk else None
    cur["data_range"] = {"from": lo, "to": hi}
    cur["targets"] = get_setting(db, "targets")
    cur["bucket"] = "week" if span.days > 45 else "day"
    cur["trend"] = _fill(_weekly(cur["series"]) if cur["bucket"] == "week" else cur["series"], s.date(), e.date(), cur["bucket"])
    cur["summary"] = _summary(cur)
    return cur


def _fill(trend: list[dict], start: date, end: date, bucket: str) -> list[dict]:
    """기록이 없는 날·주도 축에 남긴다 (생산 0, 불량률 없음) — 빈 기간을 건너뛴 선이 추세를 속이지 않게."""
    have = {t["date"]: t for t in trend}
    step = 7 if bucket == "week" else 1
    cur = start - timedelta(days=start.weekday()) if bucket == "week" else start
    out = []
    while cur <= end and len(out) < 400:
        k = cur.isoformat()
        out.append(have.get(k) or {"date": k, "produced": 0.0, "defects": 0.0, "hours": 0.0, "capa": 0.0,
                                   "stop_hours": 0.0, "rate": None, "utilization": None})
        cur += timedelta(days=step)
    return out


def _weekly(series: list[dict]) -> list[dict]:
    weeks: dict[str, dict] = {}
    for d in series:
        day = date.fromisoformat(d["date"])
        monday = (day - timedelta(days=day.weekday())).isoformat()
        w = weeks.setdefault(monday, {"date": monday, "produced": 0.0, "defects": 0.0, "hours": 0.0, "capa": 0.0, "stop_hours": 0.0})
        for k in ("produced", "defects", "hours", "capa", "stop_hours"):
            w[k] += d.get(k, 0.0)
    out = []
    for w in sorted(weeks.values(), key=lambda x: x["date"]):
        w["rate"] = w["defects"] / w["produced"] if w["produced"] else 0.0
        w["utilization"] = w["produced"] / w["capa"] if w["capa"] else None
        out.append(w)
    return out


def _summary(d: dict) -> list[str]:
    """경영진용 한 줄 요약 (최대 3줄). 숫자는 모두 이 응답 안의 값."""
    k, t, out = d["kpi"], d["targets"], []
    if not k["produced"]:
        return ["선택한 기간에 생산 기록이 없습니다."]
    causes = [c for c in d["top_causes"] if c.get("share")]
    if causes and k["confirmed_defects"]:
        c = causes[0]
        out.append(f"불량의 {c['share']:.0%}가 {c['cause'].replace(' 범위 이탈', '')} 범위 이탈 구간({c.get('range', '')})에서 늘었습니다. "
                   f"범위 밖 불량률 {c['rate_out']:.1%}, 모든 조건 정상일 때 {c['rate_in']:.1%}.")
    goal = t.get("defect_rate")
    if goal:
        gap = k["defect_rate"] - goal
        out.append(f"불량률 {k['defect_rate']:.2%} — 목표 {goal:.2%} " + (f"대비 {gap * 100:.2f}%p 높습니다." if gap > 0 else "이내입니다."))
    if k["utilization"] is not None:
        out.append(f"CAPA 가동률 {k['utilization']:.1%}, 설비 정지 {k['stops']}회 {k['stop_hours']:.1f}시간.")
    if k["pending_suspects"]:
        out.append(f"불량 판정 대기 {k['pending_suspects']}건은 아직 불량 수에 들어가지 않았습니다.")
    return out[:4]


def _compute(db: Session, start: datetime | None, end: datetime | None, equipment_id: int | None) -> dict:
    mains = _main_equipment(db)
    if equipment_id is not None:
        mains = [e for e in mains if e.id == equipment_id]
    if not mains:
        return {"equipment": [], "message": "생산 수량이 있는 설비 기록이 없습니다."}
    eq_ids = [e.id for e in mains]
    if end is None:
        end = db.scalar(select(func.max(ProcessRun.started_at)).where(ProcessRun.equipment_id.in_(eq_ids)))
    if start is None and end is not None:
        start = db.scalar(select(func.min(ProcessRun.started_at)).where(ProcessRun.equipment_id.in_(eq_ids)))
    runs = db.execute(
        select(ProcessRun, Lot).join(Lot, Lot.id == ProcessRun.lot_id)
        .where(ProcessRun.equipment_id.in_(eq_ids), ProcessRun.started_at >= start, ProcessRun.started_at <= end)
        .order_by(ProcessRun.started_at)
    ).all()
    lot_ids = [l.id for _, l in runs]
    confirmed: dict[int, float] = defaultdict(float)
    by_type: dict[str, float] = defaultdict(float)
    for i in range(0, len(lot_ids), 500):
        for lid, q, code, name, lqty in db.execute(
            select(DefectJudgment.lot_id, DefectJudgment.defect_qty, DefectType.code, DefectType.name, Lot.qty)
            .join(DefectType, DefectType.id == DefectJudgment.defect_type_id).join(Lot, Lot.id == DefectJudgment.lot_id)
            .where(DefectJudgment.lot_id.in_(lot_ids[i:i + 500]), DefectJudgment.is_cancelled.is_(False))
        ):
            qty = float(q) if q is not None else float(lqty or 1)
            confirmed[lid] = min(float(lqty or qty), confirmed[lid] + qty)
            by_type[f"{name}"] += qty

    cap_setting = get_setting(db, "capacity")
    caps = {}
    for e in mains:
        observed = [float(l.qty or 0) for r, l in runs if r.equipment_id == e.id]
        caps[e.id] = float(cap_setting.get(e.name) or (max(observed) if observed else 0))

    # 기간별 (일)
    days: dict[str, dict] = {}
    for r, l in runs:
        d = days.setdefault(r.started_at.date().isoformat(), {"date": r.started_at.date().isoformat(), "produced": 0.0,
                                                              "defects": 0.0, "hours": 0.0, "capa": 0.0})
        hours = max((r.ended_at - r.started_at).total_seconds() / 3600, 0) if r.ended_at else 1.0
        d["produced"] += float(l.qty or 1)
        d["defects"] += confirmed.get(l.id, 0.0)
        d["hours"] += hours
        d["capa"] += caps[r.equipment_id] * hours
    stops = db.scalars(select(EquipmentEvent).where(EquipmentEvent.equipment_id.in_(eq_ids),
                                                    EquipmentEvent.started_at >= start, EquipmentEvent.started_at <= end)).all()
    for ev in stops:
        d = days.setdefault(ev.started_at.date().isoformat(), {"date": ev.started_at.date().isoformat(), "produced": 0.0,
                                                               "defects": 0.0, "hours": 0.0, "capa": 0.0})
        hours = (ev.ended_at - ev.started_at).total_seconds() / 3600 if ev.ended_at else 1.0
        d["stop_hours"] = d.get("stop_hours", 0.0) + hours
        d["capa"] += caps.get(ev.equipment_id, 0.0) * hours  # 정지 시간도 계획 시간 → CAPA 에 포함
    series = []
    for d in sorted(days.values(), key=lambda x: x["date"]):
        d.setdefault("stop_hours", 0.0)
        d["rate"] = d["defects"] / d["produced"] if d["produced"] else 0.0
        d["utilization"] = d["produced"] / d["capa"] if d["capa"] else None
        series.append(d)

    produced = sum(d["produced"] for d in series)
    defects = sum(d["defects"] for d in series)
    capa = sum(d["capa"] for d in series)
    pending = db.execute(select(func.count(), func.sum(DefectSuspect.suspected_qty))
                         .where(DefectSuspect.status == "PENDING", DefectSuspect.lot_id.in_(lot_ids or [-1]))).one()

    # 불량 발견 단계: 의심을 확정한 것은 그 단계, 이력으로 적재된 것은 '이력 데이터'
    steps = dict(db.execute(select(ProcessStep.id, ProcessStep.name)).all())
    by_stage: dict[str, float] = defaultdict(float)
    via_suspect: dict[int, float] = defaultdict(float)
    for jid, step_id, q in db.execute(select(DefectSuspect.judgment_id, DefectSuspect.process_step_id, DefectSuspect.confirmed_qty)
                                      .where(DefectSuspect.status == "CONFIRMED", DefectSuspect.lot_id.in_(lot_ids or [-1]))):
        by_stage[steps.get(step_id, "미지정")] += q or 0
        via_suspect[jid] += q or 0
    direct = defects - sum(via_suspect.values())
    if direct > 0:
        by_stage["이력·직접 등록"] += direct

    by_eq: dict[int, dict] = {e.id: {"id": e.id, "name": e.name, "produced": 0.0, "defects": 0.0, "capa": 0.0} for e in mains}
    for r, l in runs:
        b = by_eq[r.equipment_id]
        hours = max((r.ended_at - r.started_at).total_seconds() / 3600, 0) if r.ended_at else 1.0
        b["produced"] += float(l.qty or 1)
        b["defects"] += confirmed.get(l.id, 0.0)
        b["capa"] += caps[r.equipment_id] * hours
    for ev in stops:
        hours = (ev.ended_at - ev.started_at).total_seconds() / 3600 if ev.ended_at else 1.0
        by_eq[ev.equipment_id]["capa"] += caps.get(ev.equipment_id, 0.0) * hours
    for b in by_eq.values():
        b["rate"] = b["defects"] / b["produced"] if b["produced"] else 0.0
        b["utilization"] = b["produced"] / b["capa"] if b["capa"] else None

    return {
        "by_equipment": list(by_eq.values()),
        "equipment": [{"id": e.id, "name": e.name, "capacity_per_hour": caps[e.id],
                       "capacity_source": "설정" if cap_setting.get(e.name) else "기록된 최대 시간당 생산 수"} for e in mains],
        "period": {"from": start, "to": end},
        "kpi": {"produced": produced, "capa": capa, "utilization": produced / capa if capa else None,
                "confirmed_defects": defects, "defect_rate": defects / produced if produced else 0.0,
                "good": produced - defects, "stop_hours": sum(d["stop_hours"] for d in series), "stops": len(stops),
                "pending_suspects": pending[0] or 0, "pending_qty": float(pending[1] or 0)},
        "series": series,
        "by_type": [{"type": k, "qty": v} for k, v in sorted(by_type.items(), key=lambda x: -x[1])],
        "by_stage": [{"stage": k, "qty": v} for k, v in sorted(by_stage.items(), key=lambda x: -x[1])],
        "top_causes": _top_causes(db, runs, confirmed),
    }


def _top_causes(db: Session, runs, confirmed: dict[int, float]) -> list[dict]:
    """조건별 '초과 불량' + 확정 원인 이력. 큰 순서."""
    run_ids = [r.id for r, _ in runs]
    lot_of = {r.id: l for r, l in runs}
    defs = {d.id: d for d in db.scalars(select(ConditionDefinition))}
    # 1) run 별 조건값과 이탈 여부
    run_conds: dict[int, dict[int, bool]] = defaultdict(dict)
    for i in range(0, len(run_ids), 500):
        for rid, did, v in db.execute(select(ConditionValue.process_run_id, ConditionValue.condition_definition_id,
                                             ConditionValue.value).where(ConditionValue.process_run_id.in_(run_ids[i:i + 500]))):
            d = defs.get(did)
            if d is None:
                continue
            lo, hi, _ = d.effective_range()
            if lo is None and hi is None:
                continue
            run_conds[rid][did] = (lo is not None and v < lo) or (hi is not None and v > hi)
    # 2) 기준 불량률 = 어떤 조건도 이탈하지 않은 시간의 불량률 (다른 조건 이탈에 섞이지 않게)
    clean = [rid for rid in run_ids if not any(run_conds.get(rid, {}).values())]
    n_clean = sum(float(lot_of[r].qty or 1) for r in clean)
    k_clean = sum(confirmed.get(lot_of[r].id, 0.0) for r in clean)
    base_rate = k_clean / n_clean if n_clean else 0.0
    per_cond: dict[int, dict] = defaultdict(lambda: {"n_out": 0.0, "k_out": 0.0, "rows_out": 0})
    for rid, conds in run_conds.items():
        lot = lot_of[rid]
        for did, out in conds.items():
            if out:
                c = per_cond[did]
                c["n_out"] += float(lot.qty or 1)
                c["k_out"] += confirmed.get(lot.id, 0.0)
                c["rows_out"] += 1
    total = sum(confirmed.values()) or 1.0
    out = []
    for did, c in per_cond.items():
        if not c["n_out"]:
            continue
        d = defs[did]
        lo, hi, learned = d.effective_range()
        rate_in = base_rate
        rate_out = c["k_out"] / c["n_out"]
        excess = max(0.0, c["k_out"] - rate_in * c["n_out"])
        if excess <= 0:
            continue
        rng = f"{lo:g}~{hi:g}" if lo is not None and hi is not None else (f"≤ {hi:g}" if hi is not None else f"≥ {lo:g}")
        out.append({"cause": f"{d.name} 범위 이탈", "kind": "조건", "node": f"cond:{did}",
                    "excess_defects": round(excess, 1), "share": excess / total, "rate_out": rate_out, "rate_in": rate_in,
                    "rows_out": c["rows_out"], "range": rng + (" (학습)" if learned else ""),
                    "evidence": f"범위 밖 {c['rows_out']}건 불량률 {rate_out:.1%} vs 모든 조건 정상 {rate_in:.1%}"})
    # 확정된 원인 이력 (실무자 판단)
    conf = db.execute(select(RootCauseConfirmation.node_type, RootCauseConfirmation.node_id, func.count())
                      .group_by(RootCauseConfirmation.node_type, RootCauseConfirmation.node_id)).all()
    for ntype, nid, n in conf:
        out.append({"cause": f"확정 원인 {ntype.value}:{nid}", "kind": "확정 이력", "node": f"{ntype.value.lower()}:{nid}",
                    "excess_defects": None, "share": None, "confirmed_count": n, "evidence": f"실무자 원인 확정 {n}건"})
    out.sort(key=lambda x: (-(x["excess_defects"] or 0), -(x.get("confirmed_count") or 0)))
    return out[:10]
