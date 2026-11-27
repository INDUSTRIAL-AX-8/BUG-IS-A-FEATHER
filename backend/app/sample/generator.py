"""시드 고정 샘플 데이터 생성기 (데모 + 통합 테스트 시나리오).

구조: 원자재 LOT(A, B, C) → 혼합 배치(병합) → 완제품 LOT(분할)

심어 둔 이상:
  1. 원자재-A 의 특정 LOT 하나(planted_raw_lot)의 순도가 낮음 → 그 LOT 이 들어간 배치의 점도 저하,
     완제품 강도 저하, 불량률 상승.
  2. 혼합기-2 가 특정 기간(anomaly window) 동안 혼합 온도가 허용 범위를 넘음 → 같은 증상.
  3. 원자재-B 수분이 시간에 따라 서서히 증가 (ARIMA 주의 부품 시나리오).
  4. 배치 하나에서 설계 비율과 다른 투입 (설계 불일치 시나리오, 불량과는 무관).

여기 나오는 부품·공정 이름은 샘플 데이터일 뿐이며 애플리케이션 로직은 이름에 의존하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.auth.provider import create_user
from app.models import (
    AuditLog,
    CandidateSnapshot,
    Characteristic,
    ConditionDefinition,
    ConditionValue,
    ControlLimit,
    DefectJudgment,
    DefectType,
    Equipment,
    ForecastResult,
    GenealogyEdge,
    ItemCategory,
    ItemType,
    Lot,
    LotStatus,
    MappingTemplate,
    Measurement,
    ProcessRun,
    ProcessStep,
    ProductDesign,
    ProductDesignComponent,
    ProductDesignStep,
    Role,
    RootCauseConfirmation,
    Setting,
    SpcViolation,
    User,
)

BASE_TIME = datetime(2026, 9, 1, 8, 0)
DAYS = 60
BATCHES_PER_DAY = 2
PRODUCTS_PER_BATCH = 4
A_LOTS_PER_BATCH_STEP = 3  # A LOT 하나가 대략 3배치마다 교체 (각 배치는 A 2개 병합)
B_BATCHES_PER_LOT = 2
C_BATCHES_PER_LOT = 4
PLANTED_A_INDEX = 15
ANOMALY_EQUIPMENT = "혼합기-2"
ANOMALY_DAYS = (38, 47)  # 이 기간(포함) 동안 혼합기-2 온도 이상
MISMATCH_BATCH_INDEX = 100

BASE_DEFECT_P = 0.03
PLANTED_DEFECT_P = 0.55
ANOMALY_DEFECT_P = 0.45

SAMPLE_USERS = [
    ("operator", "operator1234", Role.OPERATOR, "실무자(샘플)"),
    ("viewer", "viewer1234", Role.VIEWER, "조회자(샘플)"),
]


@dataclass
class Scenario:
    seed: int
    planted_raw_lot_no: str
    planted_equipment_name: str
    planted_equipment_id: int
    anomaly_from: datetime
    anomaly_to: datetime
    trend_characteristic: str
    trend_characteristic_id: int
    planted_raw_lot_id: int
    affected_batch_lot_nos: list[str] = field(default_factory=list)
    planted_defect_lot_nos: list[str] = field(default_factory=list)
    anomaly_defect_lot_nos: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    def summary(self) -> dict[str, object]:
        return {
            "심어 둔 원자재 LOT": self.planted_raw_lot_no,
            "심어 둔 설비": self.planted_equipment_name,
            "설비 이상 기간": f"{self.anomaly_from:%Y-%m-%d} ~ {self.anomaly_to:%Y-%m-%d}",
            "추세 특성": self.trend_characteristic,
            "원자재 LOT 영향 불량 예시": ", ".join(self.planted_defect_lot_nos[:3]),
            "설비 영향 불량 예시": ", ".join(self.anomaly_defect_lot_nos[:3]),
            **self.counts,
        }


_WIPE_ORDER = [
    CandidateSnapshot, RootCauseConfirmation, DefectJudgment, SpcViolation, ControlLimit,
    ForecastResult, Measurement, ConditionValue, ProcessRun, GenealogyEdge, Lot,
    ProductDesignComponent, ProductDesignStep, ProductDesign, Characteristic, ConditionDefinition,
    Equipment, ProcessStep, DefectType, ItemType, MappingTemplate, Setting, AuditLog,
]


def wipe(db: Session) -> None:
    for model in _WIPE_ORDER:
        db.execute(delete(model))
    db.commit()


def _ensure_users(db: Session) -> dict[str, User]:
    users: dict[str, User] = {}
    for username, password, role, name in SAMPLE_USERS:
        user = db.scalar(select(User).where(User.username == username))
        if user is None:
            user = create_user(db, username, password, role, name)
        users[username] = user
    return users


def generate(db: Session, seed: int = 42, reset: bool = False, run_analysis: bool = True) -> Scenario:
    if reset:
        wipe(db)
    elif db.scalar(select(Lot.id).limit(1)) is not None:
        raise RuntimeError("이미 데이터가 있습니다. --reset 으로 기존 데이터를 지운 뒤 생성하세요.")

    rng = np.random.default_rng(seed)
    users = _ensure_users(db)
    judge = users["operator"]

    # ---------- 마스터
    it = {
        "A": ItemType(name="원자재-A (주원료)", category=ItemCategory.RAW, description="샘플"),
        "B": ItemType(name="원자재-B (첨가제)", category=ItemCategory.RAW, description="샘플"),
        "C": ItemType(name="원자재-C (용매)", category=ItemCategory.RAW, description="샘플"),
        "BATCH": ItemType(name="혼합 배치", category=ItemCategory.SEMI, description="샘플"),
        "PROD": ItemType(name="완제품-P", category=ItemCategory.FINISHED, description="샘플"),
    }
    db.add_all(it.values())
    steps = {
        "IQC": ProcessStep(name="입고검사", seq=10),
        "MIX": ProcessStep(name="혼합", seq=20),
        "FILL": ProcessStep(name="충전", seq=30),
        "FQC": ProcessStep(name="최종검사", seq=40),
    }
    db.add_all(steps.values())
    db.flush()

    mixers = [
        Equipment(name=f"혼합기-{i}", process_step_id=steps["MIX"].id) for i in (1, 2, 3)
    ]
    fillers = [
        Equipment(name=f"충전기-{i}", process_step_id=steps["FILL"].id) for i in (1, 2)
    ]
    db.add_all(mixers + fillers)

    cond = {
        "temp": ConditionDefinition(name="혼합 온도", unit="℃", min_value=60, max_value=70,
                                    process_step_id=steps["MIX"].id),
        "time": ConditionDefinition(name="혼합 시간", unit="분", min_value=25, max_value=35,
                                    process_step_id=steps["MIX"].id),
        "rpm": ConditionDefinition(name="교반 속도", unit="rpm", min_value=180, max_value=220,
                                   process_step_id=steps["MIX"].id),
        "press": ConditionDefinition(name="충전 압력", unit="bar", min_value=1.8, max_value=2.2,
                                     process_step_id=steps["FILL"].id),
    }
    db.add_all(cond.values())
    db.flush()

    ch = {
        "purity": Characteristic(name="순도", unit="%", target=99.0, lsl=98.0, usl=100.0,
                                 subgroup_size=1, item_type_id=it["A"].id),
        "moisture": Characteristic(name="수분", unit="%", target=0.5, lsl=0.2, usl=0.8,
                                   subgroup_size=1, item_type_id=it["B"].id),
        "viscosity": Characteristic(name="점도", unit="cP", target=1500, lsl=1300, usl=1700,
                                    subgroup_size=1, process_step_id=steps["MIX"].id),
        "weight": Characteristic(name="중량", unit="g", target=500, lsl=495, usl=505,
                                 subgroup_size=5, item_type_id=it["PROD"].id),
        "strength": Characteristic(name="강도", unit="N", target=50, lsl=45, usl=56,
                                   subgroup_size=1, item_type_id=it["PROD"].id),
    }
    db.add_all(ch.values())

    dt = {
        "STR": DefectType(code="STR-LOW", name="강도 미달"),
        "APP": DefectType(code="APP", name="외관 불량"),
        "WT": DefectType(code="WT-OUT", name="중량 이탈"),
    }
    db.add_all(dt.values())
    db.flush()

    db.add_all([
        ProductDesign(
            name="혼합 배치 표준 배합", output_item_type_id=it["BATCH"].id, version=1,
            components=[
                ProductDesignComponent(item_type_id=it["A"].id, ratio=0.6, tolerance=0.05),
                ProductDesignComponent(item_type_id=it["B"].id, ratio=0.1, tolerance=0.03),
                ProductDesignComponent(item_type_id=it["C"].id, ratio=0.3, tolerance=0.05),
            ],
            steps=[ProductDesignStep(process_step_id=steps["MIX"].id, seq=1)],
        ),
        ProductDesign(
            name="완제품-P 표준", output_item_type_id=it["PROD"].id, version=1,
            components=[ProductDesignComponent(item_type_id=it["BATCH"].id, ratio=1.0, tolerance=0.0)],
            steps=[
                ProductDesignStep(process_step_id=steps["FILL"].id, seq=1),
                ProductDesignStep(process_step_id=steps["FQC"].id, seq=2),
            ],
        ),
    ])
    db.flush()

    n_batches = DAYS * BATCHES_PER_DAY
    n_a = n_batches // A_LOTS_PER_BATCH_STEP + 2
    n_b = n_batches // B_BATCHES_PER_LOT + 1
    n_c = n_batches // C_BATCHES_PER_LOT + 1

    measurements: list[Measurement] = []
    sub_no = {k: 0 for k in ch}

    def measure(key: str, when: datetime, values, lot_id=None, run_id=None):
        sub_no[key] += 1
        for v in np.atleast_1d(values):
            measurements.append(Measurement(
                characteristic_id=ch[key].id, lot_id=lot_id, process_run_id=run_id,
                measured_at=when, subgroup_no=sub_no[key], value=round(float(v), 4),
            ))

    # ---------- 원자재 LOT (배치 사용 시점보다 조금 앞서 입고)
    def raw_lots(prefix: str, item: ItemType, count: int, batches_per_lot: float):
        lots = []
        for i in range(count):
            first_batch = int(i * batches_per_lot)
            day = first_batch // BATCHES_PER_DAY
            created = BASE_TIME + timedelta(days=day - 2, hours=int(rng.integers(0, 8)))
            lots.append(Lot(lot_no=f"{prefix}-{i + 1:04d}", item_type_id=item.id,
                            created_at=created, qty=float(rng.integers(800, 1200))))
        db.add_all(lots)
        return lots

    a_lots = raw_lots("RA", it["A"], n_a, A_LOTS_PER_BATCH_STEP)
    b_lots = raw_lots("RB", it["B"], n_b, B_BATCHES_PER_LOT)
    c_lots = raw_lots("RC", it["C"], n_c, C_BATCHES_PER_LOT)
    db.flush()

    iqc_runs = []
    for lot in a_lots + b_lots + c_lots:
        iqc_runs.append(ProcessRun(lot_id=lot.id, process_step_id=steps["IQC"].id,
                                   started_at=lot.created_at + timedelta(hours=1),
                                   ended_at=lot.created_at + timedelta(hours=2)))
    db.add_all(iqc_runs)

    for i, lot in enumerate(a_lots):
        mean = 97.6 if i == PLANTED_A_INDEX else 99.0
        measure("purity", lot.created_at + timedelta(hours=2), rng.normal(mean, 0.25), lot_id=lot.id)
    for i, lot in enumerate(b_lots):
        trend = 0.45 + 0.005 * i
        measure("moisture", lot.created_at + timedelta(hours=2), rng.normal(trend, 0.02), lot_id=lot.id)

    anomaly_eq = mixers[1]
    anomaly_from = BASE_TIME + timedelta(days=ANOMALY_DAYS[0])
    anomaly_to = BASE_TIME + timedelta(days=ANOMALY_DAYS[1], hours=23, minutes=59)

    scenario = Scenario(
        seed=seed,
        planted_raw_lot_no=a_lots[PLANTED_A_INDEX].lot_no,
        planted_raw_lot_id=a_lots[PLANTED_A_INDEX].id,
        planted_equipment_name=anomaly_eq.name,
        planted_equipment_id=anomaly_eq.id,
        anomaly_from=anomaly_from,
        anomaly_to=anomaly_to,
        trend_characteristic=f"{it['B'].name} / {ch['moisture'].name}",
        trend_characteristic_id=ch["moisture"].id,
    )

    # ---------- 배치 + 완제품
    edges: list[GenealogyEdge] = []
    judgments: list[DefectJudgment] = []
    product_lots_all: list[Lot] = []
    for b in range(n_batches):
        day, slot = divmod(b, BATCHES_PER_DAY)
        start = BASE_TIME + timedelta(days=day, hours=1 + slot * 6)
        k = b // A_LOTS_PER_BATCH_STEP
        a_pair = [a_lots[k], a_lots[k + 1]]
        b_lot = b_lots[b // B_BATCHES_PER_LOT]
        c_lot = c_lots[b // C_BATCHES_PER_LOT]
        mixer = mixers[b % len(mixers)]

        planted = any(a.id == scenario.planted_raw_lot_id for a in a_pair)
        anomaly = mixer.id == anomaly_eq.id and anomaly_from <= start <= anomaly_to

        batch = Lot(lot_no=f"BT-{start:%y%m%d}-{slot + 1:02d}", item_type_id=it["BATCH"].id,
                    created_at=start + timedelta(hours=2), qty=1000.0)
        db.add(batch)
        db.flush()
        if planted:
            scenario.affected_batch_lot_nos.append(batch.lot_no)

        ratios = {"A": 0.6, "B": 0.1, "C": 0.3}
        if b == MISMATCH_BATCH_INDEX:
            ratios = {"A": 0.45, "B": 0.1, "C": 0.45}  # 설계 불일치
        for a in a_pair:
            edges.append(GenealogyEdge(parent_lot_id=a.id, child_lot_id=batch.id,
                                       input_ratio=ratios["A"] / 2, input_qty=ratios["A"] / 2 * 1000))
        edges.append(GenealogyEdge(parent_lot_id=b_lot.id, child_lot_id=batch.id,
                                   input_ratio=ratios["B"], input_qty=ratios["B"] * 1000))
        edges.append(GenealogyEdge(parent_lot_id=c_lot.id, child_lot_id=batch.id,
                                   input_ratio=ratios["C"], input_qty=ratios["C"] * 1000))

        mix_run = ProcessRun(lot_id=batch.id, process_step_id=steps["MIX"].id, equipment_id=mixer.id,
                             started_at=start, ended_at=start + timedelta(minutes=30),
                             operator_id=f"W{int(rng.integers(1, 6)):02d}")
        db.add(mix_run)
        db.flush()
        temp = rng.normal(73.5 if anomaly else 65.0, 1.2)
        db.add_all([
            ConditionValue(process_run_id=mix_run.id, condition_definition_id=cond["temp"].id, value=round(float(temp), 2)),
            ConditionValue(process_run_id=mix_run.id, condition_definition_id=cond["time"].id, value=round(float(rng.normal(30, 1.5)), 2)),
            ConditionValue(process_run_id=mix_run.id, condition_definition_id=cond["rpm"].id, value=round(float(rng.normal(200, 5)), 1)),
        ])
        visc_mean = 1500 - (110 if planted else 0) - (90 if anomaly else 0)
        measure("viscosity", start + timedelta(minutes=40), rng.normal(visc_mean, 25), run_id=mix_run.id)

        p_defect = BASE_DEFECT_P
        if planted:
            p_defect = max(p_defect, PLANTED_DEFECT_P)
        if anomaly:
            p_defect = max(p_defect, ANOMALY_DEFECT_P)

        for p in range(PRODUCTS_PER_BATCH):
            created = start + timedelta(hours=3, minutes=20 * p)
            prod = Lot(lot_no=f"P-{start:%y%m%d}-{slot + 1:02d}-{p + 1}", item_type_id=it["PROD"].id,
                       created_at=created, qty=250.0)
            db.add(prod)
            db.flush()
            product_lots_all.append(prod)
            edges.append(GenealogyEdge(parent_lot_id=batch.id, child_lot_id=prod.id,
                                       input_ratio=1 / PRODUCTS_PER_BATCH, input_qty=250.0))
            filler = fillers[(b + p) % len(fillers)]
            fill_run = ProcessRun(lot_id=prod.id, process_step_id=steps["FILL"].id, equipment_id=filler.id,
                                  started_at=created, ended_at=created + timedelta(minutes=15))
            fqc_run = ProcessRun(lot_id=prod.id, process_step_id=steps["FQC"].id,
                                 started_at=created + timedelta(minutes=30),
                                 ended_at=created + timedelta(minutes=40))
            db.add_all([fill_run, fqc_run])
            db.flush()
            db.add(ConditionValue(process_run_id=fill_run.id, condition_definition_id=cond["press"].id,
                                  value=round(float(rng.normal(2.0, 0.05)), 3)))
            measure("weight", created + timedelta(minutes=35), rng.normal(500, 1.2, size=5), lot_id=prod.id)
            str_mean = 50 - (4.5 if planted else 0) - (4.0 if anomaly else 0)
            measure("strength", created + timedelta(minutes=36), rng.normal(str_mean, 1.0), lot_id=prod.id)

            if rng.random() < p_defect:
                if planted or anomaly:
                    dtype = dt["STR"]
                else:
                    dtype = [dt["STR"], dt["APP"], dt["WT"]][int(rng.integers(0, 3))]
                judgments.append(DefectJudgment(
                    lot_id=prod.id, defect_type_id=dtype.id, judged_by=judge.id,
                    judged_at=created + timedelta(days=1), memo="샘플 판정", attachments=[],
                ))
                prod.status = LotStatus.DEFECT
                if planted:
                    scenario.planted_defect_lot_nos.append(prod.lot_no)
                elif anomaly:
                    scenario.anomaly_defect_lot_nos.append(prod.lot_no)

    db.add_all(edges)
    db.add_all(measurements)
    db.add_all(judgments)
    db.commit()

    scenario.counts = {
        "LOT 수": len(a_lots) + len(b_lots) + len(c_lots) + n_batches + len(product_lots_all),
        "계보 엣지 수": len(edges),
        "측정값 수": len(measurements),
        "불량 판정 수": len(judgments),
    }
    if run_analysis:
        from app.services.spc_service import recompute_all

        scenario.counts["SPC 위반 수"] = sum(recompute_all(db).values())
    return scenario


def simulate_feedback(db: Session, scenario: Scenario, confirm_prob: float = 0.85, seed: int = 0) -> dict[str, int]:
    """판정이 들어온 순서대로 '판정 시점 후보 스냅샷 → 실무자 원인 확정'을 재현한다 (정확도 지표 데모).

    심어 둔 원인이 있는 판정만 확정한다(원자재 LOT 영향 → 그 LOT, 설비 이상 → 그 설비).
    스냅샷은 그 시점까지의 판정·확정만 보고 계산하므로(as_of) 확정 이력이 쌓이는 효과가 드러난다.
    """
    from app.models import NodeType, RootCauseConfirmation
    from app.services.cause_service import analyze, save_snapshot

    rng = np.random.default_rng(seed)
    truth = {**{no: (NodeType.LOT, scenario.planted_raw_lot_id) for no in scenario.planted_defect_lot_nos},
             **{no: (NodeType.EQUIPMENT, scenario.planted_equipment_id) for no in scenario.anomaly_defect_lot_nos}}
    judge = db.scalar(select(User).where(User.username == "operator"))
    rows = db.execute(select(DefectJudgment, Lot).join(Lot, Lot.id == DefectJudgment.lot_id)
                      .order_by(DefectJudgment.judged_at)).all()
    snaps = confirms = 0
    for j, lot in rows:
        result = analyze(db, lot, as_of=j.judged_at, defect_type_id=j.defect_type_id)
        save_snapshot(db, j, result, j.judged_at)
        snaps += 1
        if lot.lot_no in truth and rng.random() < confirm_prob:
            ntype, nid = truth[lot.lot_no]
            db.add(RootCauseConfirmation(judgment_id=j.id, node_type=ntype, node_id=nid, confidence=1.0,
                                         memo="샘플: 조사 후 확정", confirmed_by=judge.id if judge else None,
                                         confirmed_at=j.judged_at + timedelta(hours=6)))
            confirms += 1
        db.commit()
    return {"스냅샷": snaps, "원인 확정": confirms}
