"""SPC 조립: 측정값 → 서브그룹 → 관리한계(고정/임시) → 넬슨룰 → 위반 저장."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.spc.charts import ChartError, ChartPoint, Limits, Subgroup, chart_points, choose_chart, compute_limits
from app.core.spc.nelson import Violation, describe, evaluate, merge_config
from app.errors import AppError, not_found
from app.models import (
    Characteristic,
    ChartType,
    ControlLimit,
    Lot,
    Measurement,
    ProcessRun,
    SpcViolation,
    User,
)
from app.services import audit
from app.services.settings_service import get_setting


def load_subgroups(
    db: Session, char: Characteristic, start: datetime | None = None, end: datetime | None = None
) -> list[Subgroup]:
    stmt = (
        select(Measurement, ProcessRun.lot_id)
        .outerjoin(ProcessRun, ProcessRun.id == Measurement.process_run_id)
        .where(Measurement.characteristic_id == char.id)
    )
    if start is not None:
        stmt = stmt.where(Measurement.measured_at >= start)
    if end is not None:
        stmt = stmt.where(Measurement.measured_at <= end)
    stmt = stmt.order_by(Measurement.measured_at, Measurement.id)
    groups: OrderedDict[tuple, Subgroup] = OrderedDict()
    for m, run_lot in db.execute(stmt):
        key = ("sg", m.subgroup_no) if m.subgroup_no is not None else ("m", m.id)
        sg = groups.get(key)
        if sg is None:
            sg = Subgroup(key=m.subgroup_no if m.subgroup_no is not None else m.id,
                          time=m.measured_at, values=[], lot_ids=[], measurement_ids=[])
            groups[key] = sg
        sg.values.append(m.value)
        sg.measurement_ids.append(m.id)
        lot_id = m.lot_id or run_lot
        if lot_id is not None and lot_id not in sg.lot_ids:
            sg.lot_ids.append(lot_id)
    return sorted(groups.values(), key=lambda s: (s.time, s.key))


def resolved_chart_type(db: Session, char: Characteristic) -> ChartType:
    cc = get_setting(db, "control_chart")
    return choose_chart(char.subgroup_size, char.chart_type, cc["imr_max_n"], cc["xbar_r_max_n"])


def locked_limit(db: Session, char_id: int) -> ControlLimit | None:
    return db.scalar(
        select(ControlLimit)
        .where(ControlLimit.characteristic_id == char_id, ControlLimit.is_locked.is_(True))
        .order_by(ControlLimit.version.desc())
        .limit(1)
    )


def _limits_from_row(row: ControlLimit, nominal_n: int) -> Limits:
    return Limits(row.chart_type, row.center, row.sigma, row.ucl, row.lcl, nominal_n,
                  row.dispersion_center, row.dispersion_ucl, row.dispersion_lcl, row.n_subgroups)


@dataclass
class SpcState:
    char: Characteristic
    chart_type: ChartType
    subgroups: list[Subgroup]
    limits: Limits | None
    limit_row: ControlLimit | None
    provisional: bool
    message: str | None
    points: list[ChartPoint]
    violations: list[Violation]
    rule_config: dict[int, dict]


def analyze(db: Session, char: Characteristic, subgroups: list[Subgroup] | None = None) -> SpcState:
    chart_type = resolved_chart_type(db, char)
    if subgroups is None:
        subgroups = load_subgroups(db, char)
    row = locked_limit(db, char.id)
    limits: Limits | None = None
    message = None
    provisional = row is None or row.chart_type != chart_type
    if not provisional:
        limits = _limits_from_row(row, char.subgroup_size)
    else:
        row = None
        n_base = get_setting(db, "control_chart")["min_baseline_subgroups"]
        try:
            limits = compute_limits(subgroups[:n_base], chart_type, char.subgroup_size)
            message = f"고정된 관리한계가 없어 처음 {min(n_base, len(subgroups))}개 서브그룹으로 계산한 임시 한계입니다."
        except ChartError as exc:
            message = str(exc)
    rule_config = merge_config(get_setting(db, "nelson_rules"), char.rule_config)
    points: list[ChartPoint] = []
    violations: list[Violation] = []
    if limits is not None and limits.sigma > 0:
        points = chart_points(subgroups, limits)
        violations = evaluate([p.value for p in points], [p.z for p in points], rule_config)
    elif limits is not None:
        message = "산포가 0 이라 관리한계를 계산할 수 없습니다."
    return SpcState(char, chart_type, subgroups, limits, row, provisional, message, points,
                    violations, rule_config)


def recompute_violations(db: Session, char: Characteristic) -> int:
    """위반을 다시 계산해 저장. 확인(ack)된 위반은 유지하고, 사라진 미확인 위반은 지운다."""
    state = analyze(db, char)
    existing = {
        (v.rule_no, v.occurred_at): v
        for v in db.scalars(select(SpcViolation).where(SpcViolation.characteristic_id == char.id))
    }
    seen: set[tuple[int, datetime]] = set()
    now = datetime.now()
    for v in state.violations:
        pts = [state.points[i] for i in v.points]
        occurred = state.points[v.first_index].subgroup.time
        key = (v.rule_no, occurred)
        if key in seen:
            continue
        seen.add(key)
        lot_ids = sorted({lid for p in pts for lid in p.subgroup.lot_ids})
        payload = [
            {"index": p.index, "subgroup": p.subgroup.key, "measured_at": p.subgroup.time.isoformat(),
             "value": round(p.value, 6), "lot_ids": p.subgroup.lot_ids}
            for p in pts
        ]
        row = existing.get(key)
        if row is None:
            db.add(SpcViolation(
                characteristic_id=char.id, control_limit_id=state.limit_row.id if state.limit_row else None,
                rule_no=v.rule_no, points=payload, lot_ids=lot_ids, occurred_at=occurred, detected_at=now,
            ))
        else:
            row.points = payload
            row.lot_ids = lot_ids
            row.control_limit_id = state.limit_row.id if state.limit_row else None
    stale = [row.id for key, row in existing.items() if key not in seen and row.acknowledged_at is None]
    if stale:
        db.execute(delete(SpcViolation).where(SpcViolation.id.in_(stale)))
    db.commit()
    return len(seen)


def recompute_all(db: Session) -> dict[int, int]:
    result = {}
    for char in db.scalars(select(Characteristic).where(Characteristic.is_active.is_(True))):
        result[char.id] = recompute_violations(db, char)
    return result


def lock_limits(
    db: Session,
    char: Characteristic,
    baseline_from: datetime,
    baseline_to: datetime,
    user: User | None,
    chart_type: ChartType | None = None,
) -> ControlLimit:
    if baseline_from >= baseline_to:
        raise AppError(422, "기준 구간의 시작이 끝보다 앞서야 합니다.")
    if chart_type is not None and chart_type != char.chart_type:
        before_char = audit.snapshot(char)
        char.chart_type = chart_type
        audit.record(db, user, "update", "characteristic", char.id, before=before_char, after=audit.snapshot(char))
    ct = resolved_chart_type(db, char)
    subgroups = load_subgroups(db, char, baseline_from, baseline_to)
    min_n = get_setting(db, "control_chart")["min_baseline_subgroups"]
    if len(subgroups) < min_n:
        raise AppError(422, f"기준 구간의 서브그룹이 {len(subgroups)}개입니다. 최소 {min_n}개가 필요합니다.")
    try:
        lim = compute_limits(subgroups, ct, char.subgroup_size)
    except ChartError as exc:
        raise AppError(422, str(exc)) from exc
    prev = locked_limit(db, char.id)
    last_version = db.scalar(
        select(ControlLimit.version).where(ControlLimit.characteristic_id == char.id)
        .order_by(ControlLimit.version.desc()).limit(1)
    ) or 0
    row = ControlLimit(
        characteristic_id=char.id, version=last_version + 1, chart_type=ct,
        baseline_from=baseline_from, baseline_to=baseline_to, center=lim.center, ucl=lim.ucl,
        lcl=lim.lcl, sigma=lim.sigma, dispersion_center=lim.dispersion_center,
        dispersion_ucl=lim.dispersion_ucl, dispersion_lcl=lim.dispersion_lcl,
        n_subgroups=lim.n_subgroups, is_locked=True, created_at=datetime.now(),
        created_by=user.id if user else None,
    )
    db.add(row)
    db.flush()
    audit.record(db, user, "recalc_limits", "control_limit", row.id,
                 before=audit.snapshot(prev) if prev else None, after=audit.snapshot(row))
    db.commit()
    recompute_violations(db, char)
    return row


def chart_payload(db: Session, char_id: int, start: datetime | None, end: datetime | None) -> dict:
    char = db.get(Characteristic, char_id)
    if char is None:
        raise not_found("측정 특성")
    state = analyze(db, char)
    lot_nos = dict(db.execute(
        select(Lot.id, Lot.lot_no).where(
            Lot.id.in_({lid for sg in state.subgroups for lid in sg.lot_ids} or {-1})
        )
    ).all())
    marks: dict[int, list[int]] = {}
    for v in state.violations:
        for i in v.points:
            marks.setdefault(i, []).append(v.rule_no)
    saved = {
        (v.rule_no, v.occurred_at): v.id
        for v in db.scalars(select(SpcViolation).where(SpcViolation.characteristic_id == char.id))
    }
    points = []
    for p in state.points:
        t = p.subgroup.time
        if (start and t < start) or (end and t > end):
            continue
        points.append({
            "index": p.index,
            "subgroup": p.subgroup.key,
            "time": t,
            "value": p.value,
            "values": p.subgroup.values,
            "dispersion": p.dispersion,
            "ucl": p.ucl,
            "lcl": p.lcl,
            "lot_ids": p.subgroup.lot_ids,
            "lot_nos": [lot_nos.get(i) for i in p.subgroup.lot_ids],
            "rules": sorted(set(marks.get(p.index, []))),
        })
    lim = state.limits
    return {
        "characteristic": {
            "id": char.id, "name": char.name, "unit": char.unit, "target": char.target,
            "usl": char.usl, "lsl": char.lsl, "subgroup_size": char.subgroup_size,
            "item_type_id": char.item_type_id, "process_step_id": char.process_step_id,
        },
        "chart_type": state.chart_type,
        "provisional": state.provisional,
        "message": state.message,
        "limits": None if lim is None else {
            "center": lim.center, "ucl": lim.ucl, "lcl": lim.lcl, "sigma": lim.sigma,
            "dispersion_center": lim.dispersion_center, "dispersion_ucl": lim.dispersion_ucl,
            "dispersion_lcl": lim.dispersion_lcl,
            "version": state.limit_row.version if state.limit_row else None,
            "baseline_from": state.limit_row.baseline_from if state.limit_row else None,
            "baseline_to": state.limit_row.baseline_to if state.limit_row else None,
        },
        "rules": [
            {"rule_no": r, "enabled": c["enabled"], "params": c["params"], "description": describe(r, c["params"])}
            for r, c in sorted(state.rule_config.items())
        ],
        "violations": [
            {
                "id": saved.get((v.rule_no, state.points[v.first_index].subgroup.time)),
                "rule_no": v.rule_no,
                "description": describe(v.rule_no, state.rule_config[v.rule_no]["params"]),
                "first_index": v.first_index,
                "last_index": v.last_index,
                "points": v.points,
                "occurred_at": state.points[v.first_index].subgroup.time,
            }
            for v in state.violations
        ],
        "points": points,
    }
