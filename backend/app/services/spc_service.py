"""SPC 조립: 측정값 → 서브그룹 → 관리한계(고정/임시) → 넬슨룰 → 위반 저장."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.spc.charts import ChartError, ChartPoint, Limits, Subgroup, chart_points, choose_chart, compute_limits
from app.core.spc.events import SpcEvent, ViolationInfo, group_events, violation_direction
from app.core.spc.nelson import Violation, describe, evaluate, merge_config
from app.errors import AppError, not_found
from app.models import (
    BaselineScope,
    Characteristic,
    GenealogyEdge,
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
    out = sorted(groups.values(), key=lambda s: (s.time, s.key))
    if char.baseline_scope == BaselineScope.PARENT_LOT:
        _assign_regimes(db, out)
    return out


def _assign_regimes(db: Session, subgroups: list[Subgroup]) -> None:
    """서브그룹의 체제 = 측정 LOT 의 상위 LOT (여러 개면 ID 가 가장 작은 것). 상위가 없으면 LOT 자신."""
    lot_ids = sorted({sg.lot_ids[0] for sg in subgroups if sg.lot_ids})
    parent: dict[int, int] = {}
    for i in range(0, len(lot_ids), 500):
        chunk = lot_ids[i : i + 500]
        for child, par in db.execute(
            select(GenealogyEdge.child_lot_id, GenealogyEdge.parent_lot_id).where(GenealogyEdge.child_lot_id.in_(chunk))
        ):
            parent[child] = min(par, parent.get(child, par))
    for sg in subgroups:
        if sg.lot_ids:
            sg.regime = parent.get(sg.lot_ids[0], sg.lot_ids[0])


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
    if char.baseline_scope == BaselineScope.PARENT_LOT:
        return _analyze_by_regime(db, char, chart_type, subgroups)
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


def _analyze_by_regime(db: Session, char: Characteristic, chart_type: ChartType,
                       subgroups: list[Subgroup]) -> SpcState:
    """체제(상위 LOT)별 기준선: 체제마다 그 체제 데이터로 관리한계를 계산하고 룰도 체제 안에서만 판정.

    KAMP 주조 분석의 '런별 기준선'을 범용화한 방식. 런(용탕·배치)마다 공정 수준이 달라
    전체 하나의 한계를 쓰면 수준 차이가 전부 위반으로 잡히는 문제를 막는다.
    """
    cc = get_setting(db, "control_chart")
    min_n = cc["min_regime_subgroups"]
    rule_config = merge_config(get_setting(db, "nelson_rules"), char.rule_config)
    groups: OrderedDict[object, list[Subgroup]] = OrderedDict()
    for sg in subgroups:
        groups.setdefault(sg.regime, []).append(sg)
    try:
        fallback = compute_limits(subgroups, chart_type, char.subgroup_size) if subgroups else None
    except ChartError:
        fallback = None

    points: list[ChartPoint] = []
    violations: list[Violation] = []
    last_limits: Limits | None = None
    small = 0
    ordered: list[Subgroup] = []
    for regime_sgs in groups.values():
        lim = None
        if len(regime_sgs) >= min_n:
            try:
                lim = compute_limits(regime_sgs, chart_type, char.subgroup_size)
            except ChartError:
                lim = None
        if lim is None or lim.sigma <= 0:
            small += 1
            lim = fallback
        if lim is None or lim.sigma <= 0:
            continue
        offset = len(points)
        local = chart_points(regime_sgs, lim)
        for p in local:
            p.index += offset
        points.extend(local)
        ordered.extend(regime_sgs)
        for v in evaluate([p.value for p in local], [p.z for p in local], rule_config):
            v.first_index += offset
            v.last_index += offset
            v.points = [i + offset for i in v.points]
            violations.append(v)
        last_limits = lim
    message = f"상위 LOT(체제)별 기준선 {len(groups)}개를 사용합니다."
    if small:
        message += f" 데이터가 {min_n}개 미만인 체제 {small}개는 전체 기준선으로 판정합니다."
    return SpcState(char, chart_type, ordered, last_limits, None, True, message, points, violations, rule_config)


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
             "value": round(p.value, 6), "z": round(p.z, 4), "lot_ids": p.subgroup.lot_ids}
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


def _event_gap(db: Session) -> int:
    return int(get_setting(db, "control_chart")["event_gap_points"])


def violation_info(row: SpcViolation) -> ViolationInfo:
    """저장된 위반 → 사건 묶기 입력. 포함 점의 z 가 없는(이전 버전에서 저장된) 위반은 방향을 모르는 것으로 본다."""
    pts = row.points or []
    indices = [int(p["index"]) for p in pts if "index" in p]
    zs = [p["z"] for p in pts if p.get("z") is not None]
    values = [p["value"] for p in pts if p.get("value") is not None]
    occurred = row.occurred_at.isoformat()
    signal = next((int(p["index"]) for p in pts if p.get("measured_at") == occurred and "index" in p),
                  min(indices, default=0))
    return ViolationInfo(
        id=row.id, characteristic_id=row.characteristic_id, rule_no=row.rule_no,
        point_indices=indices or [signal], signal_index=signal,
        direction=violation_direction(row.rule_no, zs if len(zs) == len(pts) else None, values),
        occurred_at=row.occurred_at, acknowledged=row.acknowledged_at is not None, lot_ids=list(row.lot_ids or []),
    )


def load_events(
    db: Session, characteristic_id: int | None = None, start: datetime | None = None, end: datetime | None = None,
) -> list[SpcEvent]:
    """저장된 위반을 사건으로 묶어 돌려준다 (최근 사건 먼저)."""
    stmt = select(SpcViolation)
    if characteristic_id is not None:
        stmt = stmt.where(SpcViolation.characteristic_id == characteristic_id)
    if start is not None:
        stmt = stmt.where(SpcViolation.occurred_at >= start)
    if end is not None:
        stmt = stmt.where(SpcViolation.occurred_at <= end)
    events = group_events((violation_info(r) for r in db.scalars(stmt)), _event_gap(db))
    return sorted(events, key=lambda e: e.occurred_at, reverse=True)


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
    if char.baseline_scope == BaselineScope.PARENT_LOT:
        raise AppError(409, "상위 LOT(체제)별 기준선을 쓰는 특성은 관리한계를 고정할 수 없습니다. 기준선 범위를 '전체'로 바꾼 뒤 고정하세요.")
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
            "center": p.center,
            "regime": p.subgroup.regime,
            "lot_ids": p.subgroup.lot_ids,
            "lot_nos": [lot_nos.get(i) for i in p.subgroup.lot_ids],
            "rules": sorted(set(marks.get(p.index, []))),
        })
    infos = [
        ViolationInfo(
            id=i, characteristic_id=char.id, rule_no=v.rule_no, point_indices=list(v.points), signal_index=v.first_index,
            direction=violation_direction(v.rule_no, [state.points[j].z for j in v.points],
                                          [state.points[j].value for j in v.points]),
            occurred_at=state.points[v.first_index].subgroup.time,
        )
        for i, v in enumerate(state.violations)
    ]
    events = sorted(group_events(infos, _event_gap(db)), key=lambda e: e.signal_index)
    lim = state.limits
    return {
        "characteristic": {
            "id": char.id, "name": char.name, "unit": char.unit, "target": char.target,
            "usl": char.usl, "lsl": char.lsl, "subgroup_size": char.subgroup_size,
            "item_type_id": char.item_type_id, "process_step_id": char.process_step_id,
        },
        "chart_type": state.chart_type,
        "baseline_scope": char.baseline_scope.value,
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
        # 같은 이상에 함께 걸린 위반 묶음. violations 는 위 violations 목록의 인덱스
        "events": [
            {"violations": e.violation_ids, "rule_nos": e.rule_nos, "direction": e.direction,
             "first_index": e.first_index, "last_index": e.last_index, "signal_index": e.signal_index,
             "occurred_at": e.occurred_at}
            for e in events
        ],
        "points": points,
    }
