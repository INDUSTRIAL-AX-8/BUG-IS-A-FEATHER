"""집계형 공정 기록 임포터 (예: 1시간에 한 줄 — 작업자, 측정·설정값, 생산 수, 불량 수, 최다 불량 유형).

열의 역할은 매핑 파일(JSON)로 정한다. 코드에는 특정 설비·공정·열 이름이 없다.

매핑 키
  name, equipment, process_step, item_type{name, category}, lot_prefix, row_hours
  time{date, time} 또는 time{datetime}
  operator                       : 작업자 열 (선택)
  conditions[{column, name, unit, kind(measured|setpoint), characteristic(bool), min, max}]
  produced, defects              : 생산 수, 불량 수 열
  defect_type{column, semantics} : semantics="top" 이면 그 시간 최다 유형 하나라는 뜻 (메모에 명시)
  defect_codes{표시이름: 코드}
  stop{column, contains, kind, note} : 정지 행 → 설비 이벤트
  caveats[]                      : 데이터 주의사항 → 분석 화면에 그대로 표시
  terminology                    : 화면 용어 프리셋 (general | injection | diecasting)
  capacity_per_hour              : 주 설비 시간당 생산 능력(CAPA)
  line[]                         : 전체 공정 흐름 (단계·설비·조건 평균/산포/범위·시간·검사 검출률) — 실시간 시연·3D 트윈용

변환
  한 행 = LOT 하나(수량 = 생산 수) + 공정 처리(run, 작업자) + 작업 조건값 + (특성 지정 시) 측정값
  불량 수 > 0 → 불량 판정(불량 수량 = 불량 수, 유형 = 최다 유형). LOT 상태는 전량 불량일 때만 '불량'
  정지 행 → 설비 이벤트(STOP)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingest.files import read_table
from app.ingest.validate import parse_datetime
from app.models import (
    Characteristic,
    ConditionDefinition,
    ConditionValue,
    DefectJudgment,
    DefectType,
    Equipment,
    EquipmentEvent,
    ItemCategory,
    ItemType,
    Lot,
    LotStatus,
    Measurement,
    ProcessRun,
    ProcessStep,
)
from app.sample.generator import _ensure_users, wipe
from app.services import audit
from app.services.settings_service import get_setting, put_setting


@dataclass
class ProcessLogSummary:
    rows: int
    lots: int
    events: int
    produced: float
    defects: float
    judgments: int
    skipped: list[str] = field(default_factory=list)
    equipment_id: int | None = None
    process_step_id: int | None = None

    def summary(self) -> dict[str, object]:
        rate = self.defects / self.produced if self.produced else 0
        return {"행 수": self.rows, "LOT(시간) 수": self.lots, "설비 이벤트": self.events,
                "생산": int(self.produced), "불량": int(self.defects), "불량률": f"{rate:.2%}",
                "불량 판정": self.judgments, "건너뛴 행": len(self.skipped)}


def load_mapping(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _num(v: Any) -> float | None:
    if v is None:
        return None
    t = str(v).strip().replace(",", "")
    if t in ("", "-", "—", "nan"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _get_or_create(db: Session, model, where: dict, **create):
    obj = db.scalar(select(model).filter_by(**where))
    if obj is None:
        obj = model(**where, **create)
        db.add(obj)
        db.flush()
    return obj


def _code_for(name: str, codes: dict[str, str]) -> str:
    if name in codes:
        return codes[name]
    ascii_ = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").upper()
    return ascii_ or f"T{abs(hash(name)) % 10000:04d}"


def _register_line(db: Session, mapping: dict[str, Any], main_step: ProcessStep, main_eq: Equipment, user) -> None:
    """전체 공정 흐름(line)을 마스터(공정 단계·설비·조건 항목)에 등록하고 설정에 저장."""
    line = []
    for st in mapping["line"]:
        if st.get("main"):
            main_step.seq = st.get("seq", main_step.seq)
            line.append({"step": main_step.name, "step_id": main_step.id, "equipment": main_eq.name,
                         "equipment_id": main_eq.id, "main": True})
            continue
        step = _get_or_create(db, ProcessStep, {"name": st["step"]}, seq=st.get("seq", 0))
        step.seq = st.get("seq", step.seq)
        eq = _get_or_create(db, Equipment, {"name": st["equipment"]}, process_step_id=step.id)
        conds = []
        for c in st.get("conditions", []):
            d = _get_or_create(db, ConditionDefinition, {"name": c["name"], "process_step_id": step.id},
                               unit=c.get("unit"), min_value=c.get("min"), max_value=c.get("max"))
            d.value_kind = c.get("kind")
            conds.append({**c, "id": d.id})
        line.append({"step": step.name, "step_id": step.id, "equipment": eq.name, "equipment_id": eq.id,
                     "offset_min": st.get("offset_min", 0), "minutes": st.get("minutes", 10), "conditions": conds,
                     "inspection": st.get("inspection")})
    db.commit()
    lines = dict(get_setting(db, "process_lines"))
    lines[main_eq.name] = line
    put_setting(db, "process_lines", lines, user)


def import_process_log(db: Session, csv_path: Path, mapping: dict[str, Any], reset: bool = False) -> ProcessLogSummary:
    columns, rows = read_table(Path(csv_path))
    needed = [mapping["produced"], mapping["defects"]] + [c["column"] for c in mapping.get("conditions", [])]
    t = mapping["time"]
    needed += [t["datetime"]] if "datetime" in t else [t["date"], t["time"]]
    missing = [c for c in needed if c not in columns]
    if missing:
        raise ValueError(f"파일에 없는 열입니다: {', '.join(missing)}")
    if reset:
        wipe(db)
    users = _ensure_users(db)
    hours = float(mapping.get("row_hours", 1))

    it_spec = mapping["item_type"]
    item = _get_or_create(db, ItemType, {"name": it_spec["name"]},
                          category=ItemCategory(it_spec.get("category", "FINISHED")), description=mapping.get("name"))
    step = _get_or_create(db, ProcessStep, {"name": mapping["process_step"]}, seq=10)
    eq = _get_or_create(db, Equipment, {"name": mapping["equipment"]}, process_step_id=step.id)
    conds: dict[str, ConditionDefinition] = {}
    chars: dict[str, Characteristic] = {}
    for c in mapping.get("conditions", []):
        d = _get_or_create(db, ConditionDefinition, {"name": c["name"], "process_step_id": step.id},
                           unit=c.get("unit"), min_value=c.get("min"), max_value=c.get("max"))
        d.value_kind = c.get("kind")
        conds[c["column"]] = d
        if c.get("characteristic"):
            chars[c["column"]] = _get_or_create(db, Characteristic, {"name": c["name"], "process_step_id": step.id},
                                                unit=c.get("unit"), subgroup_size=1)
    dt_spec = mapping.get("defect_type") or {}
    codes = mapping.get("defect_codes", {})
    top_semantics = dt_spec.get("semantics") == "top"
    stop = mapping.get("stop")

    summary = ProcessLogSummary(len(rows), 0, 0, 0.0, 0.0, 0, equipment_id=eq.id, process_step_id=step.id)
    prefix = mapping.get("lot_prefix", "LOG")
    for i, row in enumerate(rows, start=2):
        try:
            when = parse_datetime(row[t["datetime"]]) if "datetime" in t else parse_datetime(f"{row[t['date']]} {row[t['time']]}")
        except (ValueError, KeyError):
            summary.skipped.append(f"{i}행: 시각을 읽을 수 없음")
            continue
        end = when + timedelta(hours=hours)
        if stop and stop.get("contains") and stop["contains"] in str(row.get(stop["column"], "")):
            db.add(EquipmentEvent(equipment_id=eq.id, kind=stop.get("kind", "STOP"), started_at=when, ended_at=end,
                                  note=stop.get("note")))
            summary.events += 1
            continue
        produced = _num(row.get(mapping["produced"]))
        defects = _num(row.get(mapping["defects"])) or 0.0
        if not produced:
            summary.skipped.append(f"{i}행: 생산 수 없음")
            continue
        lot_no = f"{prefix}-{when:%Y%m%d-%H%M}"
        if db.scalar(select(Lot.id).where(Lot.lot_no == lot_no)):
            summary.skipped.append(f"{i}행: 이미 있는 LOT {lot_no}")
            continue
        lot = Lot(lot_no=lot_no, item_type_id=item.id, created_at=when, qty=produced,
                  status=LotStatus.DEFECT if defects >= produced else LotStatus.NORMAL)
        db.add(lot)
        db.flush()
        operator = str(row.get(mapping.get("operator", ""), "") or "").strip() or None
        run = ProcessRun(lot_id=lot.id, process_step_id=step.id, equipment_id=eq.id, started_at=when, ended_at=end,
                         operator_id=operator)
        db.add(run)
        db.flush()
        for col, d in conds.items():
            v = _num(row.get(col))
            if v is None:
                continue
            db.add(ConditionValue(process_run_id=run.id, condition_definition_id=d.id, value=v))
            if col in chars:
                db.add(Measurement(characteristic_id=chars[col].id, process_run_id=run.id, measured_at=when, value=v))
        if defects > 0:
            tname = str(row.get(dt_spec.get("column", ""), "") or "").strip() or "미분류"
            dtype = _get_or_create(db, DefectType, {"code": _code_for(tname, codes)}, name=tname,
                                   description="집계 기록의 최다 불량 유형" if top_semantics else None)
            memo = (f"{int(defects)}/{int(produced)}개 불량. 최다 유형: {tname}"
                    + (" (그 시간 불량이 모두 이 유형은 아님)" if top_semantics else ""))
            db.add(DefectJudgment(lot_id=lot.id, defect_type_id=dtype.id, judged_by=users["operator"].id,
                                  judged_at=end, defect_qty=defects, memo=memo, attachments=[]))
            summary.judgments += 1
        summary.lots += 1
        summary.produced += produced
        summary.defects += defects
    db.commit()

    if mapping.get("capacity_per_hour"):
        cap = dict(get_setting(db, "capacity"))
        cap[eq.name] = float(mapping["capacity_per_hour"])
        put_setting(db, "capacity", cap, users["operator"])
    if mapping.get("line"):
        _register_line(db, mapping, step, eq, users["operator"])
    if mapping.get("terminology"):
        put_setting(db, "terminology", {"preset": mapping["terminology"], "overrides": {}}, users["operator"])
    if mapping.get("caveats"):
        notes = dict(get_setting(db, "dataset_notes"))
        notes[eq.name] = list(mapping["caveats"])
        put_setting(db, "dataset_notes", notes, users["operator"])
    audit.record(db, users["operator"], "ingest", "process_log", eq.id,
                 after={"file": Path(csv_path).name, **{k: v for k, v in summary.summary().items()}})
    db.commit()

    from app.services.spc_service import recompute_all

    recompute_all(db)
    return summary
