"""실시간 수집 시뮬레이터 — 시연용. 설비 기록이 실시간 DB(RTDB)로 계속 들어오는 상황을 재현한다.

동작
  - 대상 설비의 기존 기록에서 조건값 분포(평균·표준편차), 생산 수, 작업자, 불량 유형, 근무 시간대를 배운다.
  - 요인 분석이 학습한 범위(learned_min/max)가 있으면 그 요인을 '드리프트 대상'으로 삼아, 시나리오에 따라
    서서히 범위를 벗어나게 한다. 범위 밖이면 요인 분석이 찾은 나쁜 구간 불량률, 안이면 좋은 구간 불량률로 불량을 만든다.
  - 일정 시점에 정지(정비) 이벤트를 넣고 값을 정상으로 되돌린다 → 이상 발생 · 경보 · 조치 · 회복이 한 바퀴 돈다.
  - 한 틱 = 기록 한 줄(예: 1시간). 실제 시간 interval_sec 마다 한 틱.
  - 기록은 일반 적재와 같은 테이블(LOT, 공정 처리, 조건값, 측정값, 불량 판정, 설비 이벤트)에 쓰이고, SPC 위반을 다시 계산한다.
  - 구독자(SSE)에게 기록·경보·이벤트를 바로 보낸다.

실제 RTDB/MES 연동 시에는 이 모듈 대신 수집 API(POST /api/ingest/...)로 들어온 데이터에 같은 알림을 붙이면 된다.
"""

from __future__ import annotations

import logging
import queue
import random
import threading
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import fmean, pstdev
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Characteristic,
    ConditionDefinition,
    ConditionValue,
    DefectJudgment,
    DefectType,
    Equipment,
    EquipmentEvent,
    Lot,
    LotStatus,
    Measurement,
    ProcessRun,
    SpcViolation,
    User,
)

log = logging.getLogger(__name__)

SCENARIOS = {
    "drift": "주요인이 서서히 학습 범위를 벗어났다가 정비 후 회복",
    "steady": "정상 운전 (값이 학습 범위 안에서만 흔들림)",
}


@dataclass
class Profile:
    """대상 설비 기록에서 배운 생성 규칙."""

    equipment_id: int
    equipment: str
    lot_id_template: Lot
    step_id: int
    item_type_id: int
    prefix: str
    step_hours: float
    shift: tuple[int, int] | None  # (시작 시, 마지막 시) — 기록이 특정 시간대에만 있으면 유지
    conds: dict[int, tuple[str, float, float, float | None, float | None, bool]]  # id → (이름, 평균, sd, lo, hi, learned)
    units: dict[str, str | None]
    setpoints: dict[int, float]
    produced: float
    operators: list[str]
    good_types: list[str]
    bad_types: list[str]
    drift_cond: int | None
    rate_good: float
    rate_bad: float
    chars: dict[str, int]  # 조건 이름 == 특성 이름 → 특성 id


@dataclass
class LiveState:
    running: bool = False
    equipment_id: int | None = None
    equipment: str | None = None
    scenario: str = "drift"
    interval_sec: float = 2.0
    ticks: int = 0
    sim_time: datetime | None = None
    started_at: datetime | None = None
    last_record: dict[str, Any] | None = None
    phase: str = "정상"
    message: str | None = None
    recent: list[dict] = field(default_factory=list)


class LiveSimulator:
    RECENT = 60

    def __init__(self, session_factory) -> None:
        self.session_factory = session_factory
        self.state = LiveState()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._subs: list[queue.Queue] = []
        self._profile: Profile | None = None
        self._drift_step = 0
        self._rng = random.Random()
        self._force_maintenance = False

    # ------------------------------------------------------------ 구독
    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=500)
        with self._lock:
            self._subs.append(q)
        q.put({"type": "status", **self.status()})
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def publish(self, msg: dict) -> None:
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(msg)
            except queue.Full:
                pass

    # ------------------------------------------------------------ 제어
    def status(self) -> dict[str, Any]:
        s = self.state
        return {"running": s.running, "equipment_id": s.equipment_id, "equipment": s.equipment, "scenario": s.scenario,
                "interval_sec": s.interval_sec, "ticks": s.ticks, "sim_time": s.sim_time, "phase": s.phase,
                "message": s.message, "drift_condition": self._drift_name(), "scenarios": SCENARIOS}

    def _drift_name(self) -> str | None:
        p = self._profile
        return p.conds[p.drift_cond][0] if p and p.drift_cond in p.conds else None

    def start(self, equipment_id: int | None, scenario: str = "drift", interval_sec: float = 2.0, seed: int | None = None) -> dict:
        if scenario not in SCENARIOS:
            raise ValueError(f"알 수 없는 시나리오: {scenario}")
        self.stop()
        with self.session_factory() as db:
            profile = build_profile(db, equipment_id)
        self._profile = profile
        self._rng = random.Random(seed)
        self._drift_step = 0
        st = self.state
        st.running, st.scenario, st.interval_sec = True, scenario, max(0.2, float(interval_sec))
        st.equipment_id, st.equipment = profile.equipment_id, profile.equipment
        st.ticks, st.started_at, st.phase, st.message = 0, datetime.now(), "정상", None
        with self.session_factory() as db:
            last = db.scalar(select(ProcessRun.started_at).where(ProcessRun.equipment_id == profile.equipment_id)
                             .order_by(ProcessRun.started_at.desc()).limit(1))
        st.sim_time = self._advance(last) if last else datetime.now().replace(minute=0, second=0, microsecond=0)
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="live-simulator", daemon=True)
        self._thread.start()
        self.publish({"type": "status", **self.status()})
        return self.status()

    def stop(self) -> dict:
        self._stop.set()
        if self._thread and self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=5)
        self._thread = None
        if self.state.running:
            self.state.running = False
            self.publish({"type": "status", **self.status()})
        return self.status()

    def request_maintenance(self) -> None:
        self._force_maintenance = True

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:  # 시연이 멈추지 않게 기록만
                log.exception("실시간 시뮬레이터 오류")
                self.state.message = f"오류: {exc.__class__.__name__}"
            self._stop.wait(self.state.interval_sec)

    # ------------------------------------------------------------ 한 틱
    def _advance(self, t: datetime) -> datetime:
        p = self._profile
        nxt = t + timedelta(hours=p.step_hours)
        if p.shift:
            lo, hi = p.shift
            if nxt.hour > hi or nxt.hour < lo:
                day = nxt.date() + (timedelta(days=1) if nxt.hour > hi else timedelta(0))
                nxt = datetime(day.year, day.month, day.day, lo, nxt.minute)
        return nxt

    def tick(self) -> dict:
        p = self._profile
        st = self.state
        if p is None or st.sim_time is None:
            raise RuntimeError("시뮬레이터가 시작되지 않았습니다.")
        when = st.sim_time
        rng = self._rng
        out_msgs: list[dict] = []

        # 시나리오: drift 는 12틱 동안 상승, 정비(정지) 후 회복을 반복
        maintenance = self._force_maintenance or (st.scenario == "drift" and self._drift_step >= 14)
        with self.session_factory() as db:
            if maintenance:
                self._force_maintenance = False
                self._drift_step = 0
                ev = EquipmentEvent(equipment_id=p.equipment_id, kind="STOP", started_at=when,
                                    ended_at=when + timedelta(hours=p.step_hours), note="시연: 정비 정지 (금형·온도 조절기 점검 가정)")
                db.add(ev)
                db.commit()
                st.phase = "정비"
                msg = {"type": "equipment_event", "at": when, "kind": "STOP", "equipment": p.equipment,
                       "equipment_id": p.equipment_id, "note": ev.note}
                st.sim_time = self._advance(when)
                st.ticks += 1
                self.publish(msg)
                return msg

            values: dict[int, float] = {}
            out_names: list[str] = []
            for cid, (name, mean, sd, lo, hi, learned) in p.conds.items():
                v = rng.gauss(mean, sd * 0.6)
                if cid in p.setpoints and cid != p.drift_cond:
                    v = p.setpoints[cid]
                elif cid == p.drift_cond and st.scenario == "drift":
                    span = (hi - mean) if hi is not None else (mean - lo) if lo is not None else 3 * sd
                    shift = span * (self._drift_step / 6.0)  # 6틱 즈음 경계 도달
                    v = mean + shift + rng.gauss(0, sd * 0.3) if hi is not None else mean - shift + rng.gauss(0, sd * 0.3)
                elif lo is not None or hi is not None:
                    # 드리프트 대상이 아닌 조건은 범위 안에 머무르게
                    if hi is not None:
                        v = min(v, hi - abs(rng.gauss(0, sd * 0.2)))
                    if lo is not None:
                        v = max(v, lo + abs(rng.gauss(0, sd * 0.2)))
                v = round(v, 1)
                values[cid] = v
                if (lo is not None and v < lo) or (hi is not None and v > hi):
                    out_names.append(name)
            if st.scenario == "drift":
                self._drift_step += 1

            bad = bool(out_names)
            produced = max(1, round(p.produced))
            rate = p.rate_bad if bad else p.rate_good
            defects = min(produced, max(0, round(rng.gauss(rate * produced, max(1.0, (rate * produced) ** 0.5)))))
            operator = rng.choice(p.operators) if p.operators else None

            lot = Lot(lot_no=f"{p.prefix}-{when:%Y%m%d-%H%M}", item_type_id=p.item_type_id, created_at=when,
                      qty=produced, status=LotStatus.DEFECT if defects >= produced else LotStatus.NORMAL)
            if db.scalar(select(Lot.id).where(Lot.lot_no == lot.lot_no)):
                lot.lot_no += f"-L{st.ticks}"
            db.add(lot)
            db.flush()
            run = ProcessRun(lot_id=lot.id, process_step_id=p.step_id, equipment_id=p.equipment_id, started_at=when,
                             ended_at=when + timedelta(hours=p.step_hours), operator_id=operator)
            db.add(run)
            db.flush()
            char_ids = []
            for cid, v in values.items():
                db.add(ConditionValue(process_run_id=run.id, condition_definition_id=cid, value=v))
                name = p.conds[cid][0]
                if name in p.chars:
                    db.add(Measurement(characteristic_id=p.chars[name], process_run_id=run.id, measured_at=when, value=v))
                    char_ids.append(p.chars[name])
            top = None
            if defects > 0:
                pool = p.bad_types if bad and p.bad_types else p.good_types
                top = rng.choice(pool) if pool else "미분류"
                dtype = db.scalar(select(DefectType).where(DefectType.name == top))
                judge = db.scalar(select(User.id).where(User.username == "operator"))
                if dtype is not None:
                    db.add(DefectJudgment(lot_id=lot.id, defect_type_id=dtype.id, judged_by=judge, judged_at=run.ended_at,
                                          defect_qty=defects, memo=f"실시간 수집(시연): {defects}/{produced}개, 최다 {top}",
                                          attachments=[]))
            db.commit()

            # SPC 재계산 → 새 위반 알림
            from app.services.spc_service import recompute_violations

            new_violations = []
            for chid in set(char_ids):
                before = set(db.scalars(select(SpcViolation.id).where(SpcViolation.characteristic_id == chid)))
                ch = db.get(Characteristic, chid)
                recompute_violations(db, ch)
                for v in db.scalars(select(SpcViolation).where(SpcViolation.characteristic_id == chid)):
                    if v.id not in before and lot.id in (v.lot_ids or []):
                        new_violations.append({"id": v.id, "characteristic": ch.name, "rule_no": v.rule_no,
                                               "characteristic_id": chid})
            record = {
                "type": "record", "at": when, "lot_no": lot.lot_no, "lot_id": lot.id, "equipment": p.equipment,
                "equipment_id": p.equipment_id, "operator": operator, "produced": produced, "defects": defects,
                "rate": defects / produced, "top_type": top,
                "values": {p.conds[c][0]: v for c, v in values.items()},
                "limits": {p.conds[c][0]: [p.conds[c][3], p.conds[c][4], p.conds[c][5]] for c in values},
                "units": p.units,
                "out_of_range": out_names, "violations": new_violations, "drift_condition": self._drift_name(),
            }
        st.phase = "이상" if bad or new_violations else "정상"
        st.last_record = record
        st.ticks += 1
        st.sim_time = self._advance(when)
        self.publish(record)
        for v in new_violations:
            out_msgs.append({"type": "alarm", "at": when, "lot_no": lot.lot_no, "equipment": p.equipment,
                             "text": f"{v['characteristic']} 넬슨룰 R{v['rule_no']} 위반", **v})
        if out_names:
            out_msgs.append({"type": "alarm", "at": when, "lot_no": lot.lot_no, "equipment": p.equipment,
                             "text": f"{', '.join(out_names)} 허용(학습) 범위 이탈"})
        for m in out_msgs:
            self.publish(m)
        return record


def build_profile(db: Session, equipment_id: int | None) -> Profile:
    if equipment_id is None:
        equipment_id = db.scalar(select(ProcessRun.equipment_id).where(ProcessRun.equipment_id.is_not(None))
                                 .order_by(ProcessRun.started_at.desc()).limit(1))
    eq = db.get(Equipment, equipment_id) if equipment_id else None
    if eq is None:
        raise ValueError("실시간 시연에 쓸 설비 기록이 없습니다. 먼저 데모 데이터를 넣으세요.")
    runs = db.scalars(select(ProcessRun).where(ProcessRun.equipment_id == eq.id)
                      .order_by(ProcessRun.started_at.desc()).limit(300)).all()
    if len(runs) < 3:
        raise ValueError(f"'{eq.name}' 의 기록이 너무 적습니다 (3건 이상 필요).")
    runs = list(reversed(runs))
    run_ids = [r.id for r in runs]
    lots = {l.id: l for l in db.scalars(select(Lot).where(Lot.id.in_([r.lot_id for r in runs])))}
    template = lots[runs[-1].lot_id]
    # LOT 번호 접두어: 마지막 '-' 앞부분 (없으면 설비 이름 약어)
    prefix = template.lot_no.split("-")[0] if "-" in template.lot_no else f"LIVE{eq.id}"
    gaps = sorted((b.started_at - a.started_at).total_seconds() / 3600 for a, b in zip(runs, runs[1:]))
    step_hours = gaps[len(gaps) // 2] if gaps else 1.0
    step_hours = step_hours if 0 < step_hours <= 24 else 1.0
    hours = [r.started_at.hour for r in runs]
    shift = (min(hours), max(hours)) if max(hours) - min(hours) < 20 else None

    defs = {d.id: d for d in db.scalars(select(ConditionDefinition))}
    vals: dict[int, list[tuple[datetime, float]]] = {}
    run_time = {r.id: r.started_at for r in runs}
    for rid, cid, v in db.execute(select(ConditionValue.process_run_id, ConditionValue.condition_definition_id,
                                         ConditionValue.value).where(ConditionValue.process_run_id.in_(run_ids))):
        vals.setdefault(cid, []).append((run_time[rid], v))
    conds = {}
    setpoints: dict[int, float] = {}
    drift = None
    for cid, tv in vals.items():
        d = defs[cid]
        vs = [v for _, v in sorted(tv)]
        lo, hi, learned = d.effective_range()
        # 정상 구간 값으로 평균/산포 추정 (범위가 있으면 범위 안 값만). 산포는 이동범위 기반(단기 변동)
        inside = [v for v in vs if (lo is None or v >= lo) and (hi is None or v <= hi)] or vs
        mr = [abs(b - a) for a, b in zip(inside, inside[1:])]
        sd = (fmean(mr) / 1.128) if mr and fmean(mr) > 0 else (pstdev(inside) if len(inside) > 1 else 0.0)
        sd = sd or (abs(fmean(inside)) * 0.005 or 0.1)
        conds[cid] = (d.name, fmean(inside), sd, lo, hi, learned)
        if d.value_kind == "setpoint":
            setpoints[cid] = Counter(inside).most_common(1)[0][0]  # 설정값은 평소 값 그대로
    # 드리프트 대상: 요인 분석의 '주요인' 학습 범위 > 다른 학습 범위 > 현장 허용 범위
    learned_primary = [c for c in conds if conds[c][5] and defs[c].learned_note and "주요인" in defs[c].learned_note]
    learned_any = [c for c in conds if conds[c][5]]
    ranged = [c for c, t in conds.items() if (t[3] is not None or t[4] is not None) and c not in setpoints]
    drift = (learned_primary or learned_any or ranged or [None])[0]

    produced = fmean([l.qty for l in lots.values() if l.qty]) if any(l.qty for l in lots.values()) else 1.0
    operators = sorted({r.operator_id for r in runs if r.operator_id})
    # 불량 유형: 범위 이탈 행 / 정상 행에서 따로
    jrows = db.execute(select(DefectJudgment.lot_id, DefectType.name, DefectJudgment.defect_qty)
                       .join(DefectType, DefectType.id == DefectJudgment.defect_type_id)
                       .where(DefectJudgment.lot_id.in_(list(lots)))).all()
    by_lot = {lid: (name, q) for lid, name, q in jrows}
    drift_vals = dict(db.execute(select(ConditionValue.process_run_id, ConditionValue.value)
                                 .where(ConditionValue.process_run_id.in_(run_ids),
                                        ConditionValue.condition_definition_id == drift)).all()) if drift else {}
    good_n = good_k = bad_n = bad_k = 0.0
    good_types: Counter = Counter()
    bad_types: Counter = Counter()
    for r in runs:
        lot = lots[r.lot_id]
        n = float(lot.qty or 1)
        name, q = by_lot.get(lot.id, (None, None))
        k = float(q) if q is not None else (n if name else 0.0)
        v = drift_vals.get(r.id)
        is_bad = False
        if v is not None and drift in conds:
            lo, hi = conds[drift][3], conds[drift][4]
            is_bad = (lo is not None and v < lo) or (hi is not None and v > hi)
        if is_bad:
            bad_n += n
            bad_k += k
            if name:
                bad_types[name] += 1
        else:
            good_n += n
            good_k += k
            if name:
                good_types[name] += 1
    rate_good = good_k / good_n if good_n else 0.02
    rate_bad = bad_k / bad_n if bad_n else min(0.5, rate_good * 4 + 0.02)
    chars = {c.name: c.id for c in db.scalars(select(Characteristic).where(Characteristic.process_step_id == runs[-1].process_step_id))}
    return Profile(
        equipment_id=eq.id, equipment=eq.name, lot_id_template=template, step_id=runs[-1].process_step_id,
        item_type_id=template.item_type_id, prefix=prefix, step_hours=step_hours, shift=shift, conds=conds,
        units={defs[c].name: defs[c].unit for c in conds}, setpoints=setpoints, produced=produced, operators=operators, good_types=[t for t, _ in good_types.most_common(5)],
        bad_types=[t for t, _ in bad_types.most_common(3)], drift_cond=drift, rate_good=rate_good,
        rate_bad=max(rate_bad, rate_good * 2), chars=chars,
    )


_instance: LiveSimulator | None = None


def get_simulator() -> LiveSimulator:
    global _instance
    if _instance is None:
        from app.db import SessionLocal

        _instance = LiveSimulator(SessionLocal)
    return _instance


def set_simulator(sim: LiveSimulator | None) -> None:
    """테스트용: 다른 세션 팩토리를 쓰는 시뮬레이터로 교체."""
    global _instance
    if _instance is not None:
        _instance.stop()
    _instance = sim
