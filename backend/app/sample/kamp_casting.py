"""KAMP 다이캐스팅 실데이터 임포터 (제6회 K-인공지능 제조데이터 분석 경진대회 과제 데이터).

입력 (KAMP 에서 받은 원본 CSV, 저장소에 포함하지 않음):
  casting_equipment.csv : 주조 설비 예지보전 AI 데이터셋 (SEQ 54) — 시간 순서, Machine_Status(가생산)
  casting_quality.csv   : 주조 품질보증 AI 데이터셋 (SEQ 55) — 2단 헤더, 캐비티별 결함 13종

조인과 런 분할은 KAMP_Competition/src/casting_data.py 와 같은 규칙을 따른다.
  - 런: Shot 카운터가 1씩 증가하지 않으면 새 런
  - 조인 키: Shot + 공정변수 14개 (Shot 은 런마다 리셋되므로 단독 키가 아님)

이 시스템의 범용 모델로의 매핑:
  공정 단계 : 용해 → 다이캐스팅 → 취출·검사
  계보      : 런별 용탕 LOT → shot LOT → 캐비티 제품 LOT 2개 (분할)
  설비      : 용해로-1, 다이캐스팅기-1, 금형 캐비티-1/2 (캐비티 간 불량률 차이가 원인 후보로 드러남)
  작업 조건 : 공정변수 14개 + 센서 6개 + 설비 상태(가생산). KAMP 보고서의 안정 운전 창
              (Cycle 21~34 s, Cylinder 219~264, Biscuit 13~17 mm)을 허용 범위로, 가생산=1 을 이탈로 둔다.
  측정 특성 : Nelson 감시 변수 5개 + 용해로 온도 (다이캐스팅 공정 특성, I-MR)
  불량 판정 : 품질 라벨이 있는 shot 의 캐비티별 결함 → 캐비티 제품 LOT 판정 (대표 유형 1개, 전체 유형은 메모)
  제품 ID   : YYYYMMDD + 런 문자(A~) + Shot 4자리 + -캐비티 (런별 가상 생산일, 2026-03-02 부터 하루씩)
  시각      : 원본에 시각이 없으므로 런 시작 08:00 + 누적 Cycle_Time (정지 상한 600초)

부품·공정 이름은 이 데이터셋용 매핑일 뿐이며 애플리케이션 로직은 이름에 의존하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from app.models import (
    BaselineScope,
    Characteristic,
    ConditionDefinition,
    ConditionValue,
    DefectJudgment,
    DefectType,
    Equipment,
    GenealogyEdge,
    ItemCategory,
    ItemType,
    Lot,
    LotStatus,
    Measurement,
    ProcessRun,
    ProcessStep,
    ProductDesign,
    ProductDesignComponent,
    ProductDesignStep,
    User,
)
from app.sample.generator import _ensure_users, wipe

PROC = ["Velocity_1", "Velocity_2", "Velocity_3", "High_Velocity", "Cylinder_Pressure", "Rapid_Rise_Time",
        "Biscuit_Thickness", "Clamping_Force", "Cycle_Time", "Pressure_Rise_Time", "Casting_Pressure",
        "Spray_Time", "Spray_1_Time", "Spray_2_Time"]
SENSOR = ["Melting_Furnace_Temp", "Air_Pressure", "Coolant_Temp", "Coolant_Pressure", "Factory_Temp", "Factory_Humidity"]
KEY = ["Shot"] + PROC
NELSON_VARS = ["High_Velocity", "Casting_Pressure", "Cylinder_Pressure", "Cycle_Time", "Biscuit_Thickness"]

DEFECT_TYPES: dict[str, str] = {
    "Short_Shot": "미성형", "Bubble": "기포", "Exfoliation": "박리", "Blow_Hole": "블로홀", "Stain": "얼룩",
    "Dent": "찍힘", "Deformation": "변형", "Contamination": "오염", "Impurity": "이물", "Crack": "균열",
    "Scratch": "스크래치", "Buring_Mark": "탄 자국", "Inclusions": "개재물",
}

# 변수 표시 이름과 단위
VAR_LABEL: dict[str, tuple[str, str | None]] = {
    "Velocity_1": ("저속 1구간 속도", "m/s"), "Velocity_2": ("저속 2구간 속도", "m/s"),
    "Velocity_3": ("저속 3구간 속도", "m/s"), "High_Velocity": ("고속 사출 속도", "m/s"),
    "Cylinder_Pressure": ("실린더 압력", "bar"), "Rapid_Rise_Time": ("급증압 시간", "s"),
    "Biscuit_Thickness": ("비스킷 두께", "mm"), "Clamping_Force": ("형체력", "ton"),
    "Cycle_Time": ("사이클 시간", "s"), "Pressure_Rise_Time": ("증압 시간", "s"),
    "Casting_Pressure": ("주조 압력", "bar"), "Spray_Time": ("스프레이 시간", "s"),
    "Spray_1_Time": ("스프레이 1 시간", "s"), "Spray_2_Time": ("스프레이 2 시간", "s"),
    "Melting_Furnace_Temp": ("용해로 온도", "℃"), "Air_Pressure": ("공압", "bar"),
    "Coolant_Temp": ("냉각수 온도", "℃"), "Coolant_Pressure": ("냉각수 압력", "bar"),
    "Factory_Temp": ("공장 온도", "℃"), "Factory_Humidity": ("공장 습도", "%"),
}

# KAMP 감시 규칙: R1·R5 이상 구간, R2·R6 경고. 공정변수가 강하게 양자화돼 있어
# (Cylinder_Pressure 고유값 6개, Biscuit_Thickness MAD=0) 추세·교대·밀집 규칙(R3·R4·R7·R8)은 끈다.
KAMP_RULES = {str(r): {"enabled": r in (1, 2, 5, 6)} for r in range(1, 9)}

# KAMP 보고서(docs/kpi_definition.md) 조치기준의 '안정 운전 창'
STABLE_WINDOW: dict[str, tuple[float, float]] = {
    "Cycle_Time": (21.0, 34.0),
    "Cylinder_Pressure": (219.0, 264.0),
    "Biscuit_Thickness": (13.0, 17.0),
}
WARMUP_COND = "설비 상태(가생산)"

BASE_DATE = datetime(2026, 3, 2, 8, 0)
CYCLE_CAP_SECONDS = 600.0  # 장사이클 정지 상한 (KAMP KPI 정의와 동일)
CAVITIES = (1, 2)
JUDGE_DELAY = timedelta(hours=1)
KAMP_DOWNSTREAM_DAYS = 1


# ------------------------------------------------------------------ 읽기/조인

def read_any(path: Path) -> pd.DataFrame:
    df = None
    for enc in ("utf-8-sig", "cp949", "euc-kr"):
        try:
            df = pd.read_csv(path, encoding=enc, header=None, low_memory=False)
            break
        except UnicodeDecodeError:
            continue
    if df is None:
        raise ValueError(f"파일 인코딩을 읽을 수 없습니다: {path}")
    first = set(df.iloc[0].astype(str).str.strip().unique())
    if first <= {"Process", "Sensor", "Defects"}:  # 품질 데이터의 2단 헤더
        cols, df = df.iloc[1].astype(str).str.strip().tolist(), df.iloc[2:].reset_index(drop=True)
    else:
        cols, df = df.iloc[0].astype(str).str.strip().tolist(), df.iloc[1:].reset_index(drop=True)
    df.columns = cols
    for c in df.columns:
        converted = pd.to_numeric(df[c], errors="coerce")
        if converted.notna().sum() >= df[c].notna().sum() * 0.9:
            df[c] = converted
    return df


def load_equipment(path: Path) -> pd.DataFrame:
    e = read_any(path)
    e["run"] = (e["Shot"].diff().fillna(1) != 1).cumsum().astype(int)
    e["seq"] = np.arange(len(e))
    return e


def load_quality(path: Path) -> pd.DataFrame:
    q = read_any(path)
    dcols = [c for c in q.columns if any(c.startswith(t + "_") for t in DEFECT_TYPES)]
    D = q[dcols].apply(pd.to_numeric, errors="coerce").fillna(0) > 0
    for cav in CAVITIES:
        cols = [c for c in dcols if c.endswith(f"_{cav}")]
        q[f"types_{cav}"] = D[cols].apply(lambda r: [c.rsplit("_", 1)[0] for c, v in r.items() if v], axis=1)
    return q


def join_shots(e: pd.DataFrame, q: pd.DataFrame) -> pd.DataFrame:
    """설비 행(시간순) 기준으로 품질을 붙인다. 품질 복제 행은 설비 행당 1개로 축약."""
    keep = KEY + ["Product_Type", "types_1", "types_2"] + [c for c in SENSOR if c in q.columns]
    qd = q.drop_duplicates(KEY)[keep]
    j = e.merge(qd, on=KEY, how="left", validate="m:1")
    j["has_quality"] = j["types_1"].notna()
    j["Product_Type"] = j.groupby("run")["Product_Type"].transform(
        lambda s: s.dropna().mode().iloc[0] if s.notna().any() else np.nan)
    return j.sort_values("seq").reset_index(drop=True)


# ------------------------------------------------------------------ 적재

@dataclass
class KampSummary:
    runs: int
    shots: int
    inspected_shots: int
    lots: int
    judgments: int
    defect_rate_cavity: dict[int, float] = field(default_factory=dict)
    run_lots: dict[int, str] = field(default_factory=dict)
    run_defect_rate: dict[int, float] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)

    def summary(self) -> dict[str, object]:
        return {
            "런 수": self.runs, "shot 수": self.shots, "품질 라벨 shot": self.inspected_shots,
            "LOT 수": self.lots, "불량 판정 수(캐비티)": self.judgments,
            "캐비티별 불량률": ", ".join(f"{k}: {v:.1%}" for k, v in self.defect_rate_cavity.items()),
            **self.counts,
        }


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if np.isfinite(f) else None


def import_kamp(
    db: Session, raw_dir: Path, reset: bool = False, run_analysis: bool = True, max_shots: int | None = None
) -> KampSummary:
    raw_dir = Path(raw_dir)
    eq_path, q_path = raw_dir / "casting_equipment.csv", raw_dir / "casting_quality.csv"
    for p in (eq_path, q_path):
        if not p.is_file():
            raise FileNotFoundError(f"KAMP 원본 파일이 없습니다: {p}")
    if reset:
        wipe(db)
    elif db.scalar(select(Lot.id).limit(1)) is not None:
        raise RuntimeError("이미 데이터가 있습니다. --reset 으로 기존 데이터를 지운 뒤 가져오세요.")

    j = join_shots(load_equipment(eq_path), load_quality(q_path))
    if max_shots:
        j = j.head(max_shots)
    users = _ensure_users(db)
    judge: User = users["operator"]

    # ---------- 마스터
    melt = ItemType(name="알루미늄 용탕", category=ItemCategory.RAW, description="KAMP 주조 · 런별 용탕")
    shot_t = ItemType(name="다이캐스팅 shot", category=ItemCategory.SEMI, description="KAMP 주조 · 1 shot = 2 캐비티")
    ptypes = sorted(int(p) for p in j["Product_Type"].dropna().unique()) or [1]
    products = {p: ItemType(name=f"주조품 Type{p}", category=ItemCategory.FINISHED, description="KAMP 주조 · 캐비티 제품")
                for p in ptypes}
    db.add_all([melt, shot_t, *products.values()])
    s_melt, s_cast, s_take = (ProcessStep(name="용해", seq=10), ProcessStep(name="다이캐스팅", seq=20),
                              ProcessStep(name="취출·검사", seq=30))
    db.add_all([s_melt, s_cast, s_take])
    db.flush()
    furnace = Equipment(name="용해로-1", process_step_id=s_melt.id)
    machine = Equipment(name="다이캐스팅기-1", process_step_id=s_cast.id)
    cavity_eq = {c: Equipment(name=f"금형 캐비티-{c}", process_step_id=s_take.id) for c in CAVITIES}
    db.add_all([furnace, machine, *cavity_eq.values()])

    conds: dict[str, ConditionDefinition] = {}
    for var in PROC + SENSOR:
        if var not in j.columns:
            continue
        label, unit = VAR_LABEL[var]
        lo, hi = STABLE_WINDOW.get(var, (None, None))
        conds[var] = ConditionDefinition(name=label, unit=unit, min_value=lo, max_value=hi, process_step_id=s_cast.id)
    conds["Machine_Status"] = ConditionDefinition(name=WARMUP_COND, unit=None, min_value=0, max_value=0,
                                                  process_step_id=s_cast.id)
    db.add_all(conds.values())

    chars: dict[str, Characteristic] = {}
    for var in NELSON_VARS + ["Melting_Furnace_Temp"]:
        label, unit = VAR_LABEL[var]
        lo, hi = STABLE_WINDOW.get(var, (None, None))
        chars[var] = Characteristic(name=label, unit=unit, lsl=lo, usl=hi, subgroup_size=1,
                                    process_step_id=s_cast.id, rule_config=KAMP_RULES,
                                    baseline_scope=BaselineScope.PARENT_LOT)  # 런(용탕 LOT)별 기준선
    db.add_all(chars.values())
    dtypes = {code: DefectType(code=code.upper(), name=name, description=f"KAMP 결함 컬럼 {code}_1/_2")
              for code, name in DEFECT_TYPES.items()}
    db.add_all(dtypes.values())
    db.flush()

    db.add(ProductDesign(name="shot 표준", output_item_type_id=shot_t.id, version=1,
                         components=[ProductDesignComponent(item_type_id=melt.id, ratio=1.0, tolerance=0.0)],
                         steps=[ProductDesignStep(process_step_id=s_cast.id, seq=1)]))
    for p, it in products.items():
        db.add(ProductDesign(name=f"주조품 Type{p} 표준", output_item_type_id=it.id, version=1,
                             components=[ProductDesignComponent(item_type_id=shot_t.id, ratio=1.0, tolerance=0.0)],
                             steps=[ProductDesignStep(process_step_id=s_take.id, seq=1)]))
    db.flush()

    # ---------- 시각: 런 시작 + 누적 사이클
    cycle = j["Cycle_Time"].astype(float)
    cycle = cycle.fillna(cycle.median()).clip(lower=1.0, upper=CYCLE_CAP_SECONDS)
    runs = sorted(j["run"].unique())
    run_index = {r: i for i, r in enumerate(runs)}
    start_of_run = {r: BASE_DATE + timedelta(days=i) for r, i in run_index.items()}
    j["t"] = [start_of_run[r] + timedelta(seconds=float(s)) for r, s in
              zip(j["run"], cycle.groupby(j["run"]).cumsum() - cycle)]

    def run_code(r: int) -> str:
        i = run_index[r]
        letter = chr(ord("A") + i) if i < 26 else f"R{i}"
        return f"{start_of_run[r]:%Y%m%d}{letter}"

    # ---------- LOT
    melt_lots = {r: Lot(lot_no=f"M{run_code(r)}", item_type_id=melt.id,
                        created_at=start_of_run[r] - timedelta(hours=1), qty=None) for r in runs}
    db.add_all(melt_lots.values())
    db.flush()

    shot_lots: list[Lot] = []
    prod_lots: list[tuple[int, int, Lot]] = []  # (row index, cavity, lot)
    for i, row in enumerate(j.itertuples(index=False)):
        sid = f"{run_code(row.run)}{int(row.Shot):04d}"
        shot_lots.append(Lot(lot_no=sid, item_type_id=shot_t.id, created_at=row.t, qty=None))
        ptype = products.get(int(row.Product_Type)) if pd.notna(row.Product_Type) else None
        ptype = ptype or next(iter(products.values()))
        for cav in CAVITIES:
            prod_lots.append((i, cav, Lot(lot_no=f"{sid}-{cav}", item_type_id=ptype.id,
                                          created_at=row.t + timedelta(seconds=5), qty=1.0)))
    db.add_all(shot_lots)
    db.add_all([l for _, _, l in prod_lots])
    db.flush()

    db.execute(insert(GenealogyEdge), [
        *({"parent_lot_id": melt_lots[r].id, "child_lot_id": shot_lots[i].id, "input_ratio": 1.0, "input_qty": None}
          for i, r in enumerate(j["run"])),
        *({"parent_lot_id": shot_lots[i].id, "child_lot_id": l.id, "input_ratio": 1 / len(CAVITIES), "input_qty": None}
          for i, _, l in prod_lots),
    ])

    # ---------- 공정 처리
    melt_runs = [ProcessRun(lot_id=melt_lots[r].id, process_step_id=s_melt.id, equipment_id=furnace.id,
                            started_at=start_of_run[r] - timedelta(hours=1), ended_at=start_of_run[r])
                 for r in runs]
    cast_runs = [ProcessRun(lot_id=lot.id, process_step_id=s_cast.id, equipment_id=machine.id,
                            started_at=lot.created_at,
                            ended_at=lot.created_at + timedelta(seconds=float(cycle.iloc[i])))
                 for i, lot in enumerate(shot_lots)]
    take_runs = [ProcessRun(lot_id=l.id, process_step_id=s_take.id, equipment_id=cavity_eq[cav].id,
                            started_at=l.created_at, ended_at=l.created_at + timedelta(seconds=10))
                 for _, cav, l in prod_lots]
    db.add_all(melt_runs + cast_runs + take_runs)
    db.flush()

    cond_rows = []
    meas_rows = []
    for i, row in enumerate(j.to_dict("records")):
        run_id = cast_runs[i].id
        for var, d in conds.items():
            v = _num(row.get(var))
            if v is not None:
                cond_rows.append({"process_run_id": run_id, "condition_definition_id": d.id, "value": v})
        for var, c in chars.items():
            v = _num(row.get(var))
            if v is not None:
                meas_rows.append({"characteristic_id": c.id, "lot_id": None, "process_run_id": run_id,
                                  "measured_at": row["t"], "subgroup_no": None, "value": v})
    for k in range(0, len(cond_rows), 20000):
        db.execute(insert(ConditionValue), cond_rows[k : k + 20000])
    for k in range(0, len(meas_rows), 20000):
        db.execute(insert(Measurement), meas_rows[k : k + 20000])

    # ---------- 불량 판정 (품질 라벨 있는 shot 의 캐비티별 결함)
    judgments = []
    defect_by_cav = {c: [0, 0] for c in CAVITIES}
    run_stats: dict[int, list[int]] = {r: [0, 0] for r in runs}
    for i, cav, lot in prod_lots:
        row = j.iloc[i]
        if not row["has_quality"]:
            continue
        types = row[f"types_{cav}"] or []
        defect_by_cav[cav][1] += 1
        run_stats[row["run"]][1] += 1
        if not types:
            continue
        defect_by_cav[cav][0] += 1
        run_stats[row["run"]][0] += 1
        lot.status = LotStatus.DEFECT
        judgments.append(DefectJudgment(
            lot_id=lot.id, defect_type_id=dtypes[types[0]].id, judged_by=judge.id,
            judged_at=lot.created_at + JUDGE_DELAY, attachments=[],
            memo="KAMP 품질 라벨: " + ", ".join(f"{DEFECT_TYPES[t]}({t})" for t in types),
        ))
    db.add_all(judgments)
    db.commit()
    # 이 데이터는 런 하나가 하루(가상 생산일)이므로 설비·공정 영향 범위를 ±1일로 둔다 (현장 설정값).
    from app.services.settings_service import get_setting, put_setting

    scoring = get_setting(db, "cause_scoring")
    if scoring["downstream_window_days"] != KAMP_DOWNSTREAM_DAYS:
        put_setting(db, "cause_scoring", {"downstream_window_days": KAMP_DOWNSTREAM_DAYS}, judge)

    summary = KampSummary(
        runs=len(runs), shots=len(j), inspected_shots=int(j["has_quality"].sum()),
        lots=len(melt_lots) + len(shot_lots) + len(prod_lots), judgments=len(judgments),
        defect_rate_cavity={c: (d / n if n else 0.0) for c, (d, n) in defect_by_cav.items()},
        run_lots={int(r): melt_lots[r].lot_no for r in runs},
        run_defect_rate={int(r): (d / n if n else 0.0) for r, (d, n) in run_stats.items()},
        counts={"작업 조건값 수": len(cond_rows), "측정값 수": len(meas_rows)},
    )
    if run_analysis:
        from app.services.spc_service import recompute_all

        summary.counts["SPC 위반 수"] = sum(recompute_all(db).values())
    return summary
