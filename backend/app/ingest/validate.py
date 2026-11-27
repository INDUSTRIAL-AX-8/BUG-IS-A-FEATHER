"""업로드와 REST 수집이 함께 쓰는 검증·적재 로직.

대상(target)마다 시스템 필드 정의가 있고, 행은 {시스템 필드: 원본 문자열} 형태로 들어온다.
검증: 필수값 누락, 형식(숫자/날짜), 참조(존재하지 않는 LOT·부품 종류·공정 등 — 같은 묶음 안에서 먼저 나온 행 포함),
     중복, 순환 계보. 오류 행은 따로 돌려주고 정상 행만 적재한다.

작업 조건/측정값은 열 이름 대신 동적 필드를 쓴다:
  runs 대상: "cond:<조건 항목 이름>" 필드 → ConditionValue
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.genealogy.dag import would_create_cycle
from app.models import (
    Characteristic,
    ConditionDefinition,
    ConditionValue,
    DefectJudgment,
    DefectType,
    Equipment,
    GenealogyEdge,
    ItemType,
    Lot,
    LotStatus,
    Measurement,
    ProcessRun,
    ProcessStep,
    User,
)
from app.services import audit
from app.services.genealogy_service import children_of


@dataclass(frozen=True)
class FieldSpec:
    name: str
    label: str
    kind: str = "str"  # str | float | int | datetime | status
    required: bool = False


TARGETS: dict[str, dict[str, Any]] = {
    "lots": {
        "label": "LOT",
        "fields": [
            FieldSpec("lot_no", "LOT 번호", required=True),
            FieldSpec("item_type", "부품 종류(이름)", required=True),
            FieldSpec("created_at", "생성 시각", "datetime", required=True),
            FieldSpec("qty", "수량", "float"),
            FieldSpec("status", "상태(정상/보류/격리/불량)", "status"),
        ],
    },
    "edges": {
        "label": "계보(투입 관계)",
        "fields": [
            FieldSpec("parent_lot_no", "투입 LOT(상위)", required=True),
            FieldSpec("child_lot_no", "생산 LOT(하위)", required=True),
            FieldSpec("input_qty", "투입 수량", "float"),
            FieldSpec("input_ratio", "투입 비율(0~1)", "float"),
        ],
    },
    "runs": {
        "label": "공정 처리 + 작업 조건",
        "fields": [
            FieldSpec("lot_no", "LOT 번호", required=True),
            FieldSpec("process_step", "공정 단계(이름)", required=True),
            FieldSpec("equipment", "설비(이름)"),
            FieldSpec("started_at", "시작 시각", "datetime", required=True),
            FieldSpec("ended_at", "종료 시각", "datetime"),
            FieldSpec("operator_id", "작업자 ID"),
        ],
        "dynamic": "cond",  # cond:<조건 항목 이름>
    },
    "measurements": {
        "label": "측정값",
        "fields": [
            FieldSpec("characteristic", "측정 특성(이름 또는 ID)", required=True),
            FieldSpec("lot_no", "LOT 번호", required=True),
            FieldSpec("measured_at", "측정 시각", "datetime", required=True),
            FieldSpec("value", "값", "float", required=True),
            FieldSpec("subgroup_no", "서브그룹 번호", "int"),
            FieldSpec("process_step", "공정 단계(공정 특성일 때 run 연결)"),
        ],
    },
    "defects": {
        "label": "불량 판정 이력",
        "fields": [
            FieldSpec("lot_no", "LOT 번호", required=True),
            FieldSpec("defect_type", "불량 유형 코드", required=True),
            FieldSpec("judged_at", "판정 시각", "datetime", required=True),
            FieldSpec("memo", "메모"),
        ],
    },
}

STATUS_ALIASES = {
    "정상": LotStatus.NORMAL, "보류": LotStatus.HOLD, "격리": LotStatus.QUARANTINE, "불량": LotStatus.DEFECT,
    **{s.value: s for s in LotStatus},
}

_DT_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y-%m-%d", "%Y/%m/%d",
               "%Y%m%d%H%M%S", "%Y%m%d")


def parse_datetime(text: str) -> datetime:
    t = text.strip().replace("T", " ")
    if "." in t and t.rsplit(".", 1)[1].isdigit():
        t = t.rsplit(".", 1)[0]
    for fmt in _DT_FORMATS:
        try:
            return datetime.strptime(t, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.strip())
    except ValueError:
        raise ValueError("날짜/시각 형식이 아닙니다 (예: 2026-03-02 08:00)") from None


def _convert(spec: FieldSpec, raw: Any) -> Any:
    if raw is None or (isinstance(raw, float) and raw != raw):
        return None
    text = str(raw).strip()
    if text == "" or text.lower() in ("nan", "none", "null"):
        return None
    if spec.kind == "float" or spec.kind.startswith("cond"):
        try:
            return float(text.replace(",", ""))
        except ValueError:
            raise ValueError("숫자가 아닙니다") from None
    if spec.kind == "int":
        try:
            f = float(text)
        except ValueError:
            raise ValueError("정수가 아닙니다") from None
        if f != int(f):
            raise ValueError("정수가 아닙니다")
        return int(f)
    if spec.kind == "datetime":
        if isinstance(raw, datetime):
            return raw
        return parse_datetime(text)
    if spec.kind == "status":
        if text not in STATUS_ALIASES:
            raise ValueError("상태는 정상/보류/격리/불량 중 하나여야 합니다")
        return STATUS_ALIASES[text]
    return text


@dataclass
class RowError:
    row: int  # 원본 기준 1부터 (헤더 제외)
    messages: list[str]
    data: dict[str, Any]


@dataclass
class ValidationResult:
    target: str
    total: int
    valid: list[tuple[int, dict[str, Any]]] = field(default_factory=list)
    errors: list[RowError] = field(default_factory=list)

    def summary(self, preview: int = 0) -> dict[str, Any]:
        return {
            "target": self.target, "total": self.total, "valid": len(self.valid), "invalid": len(self.errors),
            "errors": [{"row": e.row, "messages": e.messages} for e in self.errors[:200]],
            "preview": [{"row": r, **{k: (v.value if isinstance(v, LotStatus) else v.isoformat(sep=" ") if isinstance(v, datetime) else v)
                                      for k, v in d.items()}} for r, d in self.valid[:preview]],
        }


class _Lookup:
    """DB 참조 캐시 (이름 → ID)."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.item_types = {n: i for i, n in db.execute(select(ItemType.id, ItemType.name))}
        self.steps = {n: i for i, n in db.execute(select(ProcessStep.id, ProcessStep.name))}
        self.equipment = {n: i for i, n in db.execute(select(Equipment.id, Equipment.name))}
        self.defect_types = {c: i for i, c in db.execute(select(DefectType.id, DefectType.code))}
        self.chars_by_name: dict[str, list[int]] = defaultdict(list)
        self.char_step: dict[int, int | None] = {}
        for c in db.scalars(select(Characteristic)):
            self.chars_by_name[c.name].append(c.id)
            self.char_step[c.id] = c.process_step_id
        self.conds: dict[str, list[ConditionDefinition]] = defaultdict(list)
        for d in db.scalars(select(ConditionDefinition)):
            self.conds[d.name].append(d)
        self._lots: dict[str, int | None] = {}

    def lot_id(self, lot_no: str) -> int | None:
        if lot_no not in self._lots:
            self._lots[lot_no] = self.db.scalar(select(Lot.id).where(Lot.lot_no == lot_no))
        return self._lots[lot_no]


def validate_rows(db: Session, target: str, rows: list[dict[str, Any]], start_row: int = 1) -> ValidationResult:
    if target not in TARGETS:
        raise ValueError(f"알 수 없는 대상입니다: {target}")
    spec = TARGETS[target]
    fields = {f.name: f for f in spec["fields"]}
    lk = _Lookup(db)
    res = ValidationResult(target, len(rows))
    batch_lots: set[str] = set()
    pending_edges: dict[int | str, list[int | str]] = defaultdict(list)
    seen_edges: set[tuple[str, str]] = set()
    db_children = children_of(db)

    def children(ids):
        out = db_children({i for i in ids if isinstance(i, int)})
        merged = defaultdict(list, {k: list(v) for k, v in out.items()})
        for i in ids:
            merged[i].extend(pending_edges.get(i, []))
        return merged

    for offset, raw in enumerate(rows):
        rowno = start_row + offset
        errs: list[str] = []
        data: dict[str, Any] = {}
        for name, f in fields.items():
            try:
                data[name] = _convert(f, raw.get(name))
            except ValueError as exc:
                errs.append(f"{f.label}: {exc}")
                continue
            if f.required and data[name] is None:
                errs.append(f"{f.label}: 필수값 누락")
        if spec.get("dynamic") == "cond":
            for key, value in raw.items():
                if not str(key).startswith("cond:"):
                    continue
                cname = str(key)[5:]
                try:
                    v = _convert(FieldSpec(key, cname, "float"), value)
                except ValueError as exc:
                    errs.append(f"조건 '{cname}': {exc}")
                    continue
                if v is not None:
                    data[key] = v
        if not errs:
            errs.extend(_check_refs(target, data, lk, batch_lots, pending_edges, seen_edges, children))
        if errs:
            res.errors.append(RowError(rowno, errs, {k: raw.get(k) for k in raw}))
        else:
            res.valid.append((rowno, data))
            if target == "lots":
                batch_lots.add(data["lot_no"])
    return res


def _lot_ref(lk: _Lookup, batch_lots: set[str], lot_no: str) -> int | str | None:
    lid = lk.lot_id(lot_no)
    if lid is not None:
        return lid
    return lot_no if lot_no in batch_lots else None


def _check_refs(target, d, lk: _Lookup, batch_lots, pending_edges, seen_edges, children) -> list[str]:
    errs: list[str] = []
    if target == "lots":
        if d["item_type"] not in lk.item_types:
            errs.append(f"부품 종류: 등록되지 않은 이름 '{d['item_type']}' (마스터 관리에서 먼저 등록)")
        if lk.lot_id(d["lot_no"]) is not None or d["lot_no"] in batch_lots:
            errs.append(f"LOT 번호: 이미 있는 LOT '{d['lot_no']}'")
    elif target == "edges":
        p = lk.lot_id(d["parent_lot_no"])
        c = lk.lot_id(d["child_lot_no"])
        if p is None:
            errs.append(f"투입 LOT: 존재하지 않는 LOT '{d['parent_lot_no']}'")
        if c is None:
            errs.append(f"생산 LOT: 존재하지 않는 LOT '{d['child_lot_no']}'")
        ratio = d.get("input_ratio")
        if ratio is not None and not 0 <= ratio <= 1:
            errs.append("투입 비율: 0~1 사이여야 합니다")
        if not errs:
            key = (d["parent_lot_no"], d["child_lot_no"])
            if key in seen_edges:
                errs.append("같은 투입 관계가 파일에 중복되어 있습니다")
            elif would_create_cycle(p, c, children):
                errs.append(f"순환 계보: '{d['child_lot_no']}' 이(가) 이미 '{d['parent_lot_no']}' 의 상위에 있습니다")
            else:
                seen_edges.add(key)
                pending_edges[p].append(c)
    elif target == "runs":
        if lk.lot_id(d["lot_no"]) is None:
            errs.append(f"LOT 번호: 존재하지 않는 LOT '{d['lot_no']}'")
        if d["process_step"] not in lk.steps:
            errs.append(f"공정 단계: 등록되지 않은 이름 '{d['process_step']}'")
        if d.get("equipment") and d["equipment"] not in lk.equipment:
            errs.append(f"설비: 등록되지 않은 이름 '{d['equipment']}'")
        if d.get("ended_at") and d["ended_at"] < d["started_at"]:
            errs.append("종료 시각이 시작 시각보다 빠릅니다")
        for key in [k for k in d if k.startswith("cond:")]:
            cname = key[5:]
            if cname not in lk.conds:
                errs.append(f"조건 항목: 등록되지 않은 이름 '{cname}'")
    elif target == "measurements":
        c = d["characteristic"]
        ids = [int(c)] if c.isdigit() and int(c) in lk.char_step else lk.chars_by_name.get(c, [])
        if not ids:
            errs.append(f"측정 특성: 등록되지 않은 특성 '{c}'")
        elif len(ids) > 1:
            errs.append(f"측정 특성: 이름 '{c}' 이(가) 여러 개입니다. ID 로 지정하세요")
        else:
            d["_char_id"] = ids[0]
        if lk.lot_id(d["lot_no"]) is None:
            errs.append(f"LOT 번호: 존재하지 않는 LOT '{d['lot_no']}'")
        if d.get("process_step") and d["process_step"] not in lk.steps:
            errs.append(f"공정 단계: 등록되지 않은 이름 '{d['process_step']}'")
    elif target == "defects":
        if lk.lot_id(d["lot_no"]) is None:
            errs.append(f"LOT 번호: 존재하지 않는 LOT '{d['lot_no']}'")
        if d["defect_type"] not in lk.defect_types:
            errs.append(f"불량 유형: 등록되지 않은 코드 '{d['defect_type']}'")
    return errs


def commit_rows(db: Session, res: ValidationResult, user: User | None, source: str) -> int:
    """검증 통과 행만 적재. 반환: 적재 행 수."""
    lk = _Lookup(db)
    n = 0
    t = res.target
    for _, d in res.valid:
        if t == "lots":
            db.add(Lot(lot_no=d["lot_no"], item_type_id=lk.item_types[d["item_type"]], created_at=d["created_at"],
                       qty=d.get("qty"), status=d.get("status") or LotStatus.NORMAL))
        elif t == "edges":
            db.add(GenealogyEdge(parent_lot_id=lk.lot_id(d["parent_lot_no"]), child_lot_id=lk.lot_id(d["child_lot_no"]),
                                 input_qty=d.get("input_qty"), input_ratio=d.get("input_ratio")))
        elif t == "runs":
            run = ProcessRun(lot_id=lk.lot_id(d["lot_no"]), process_step_id=lk.steps[d["process_step"]],
                             equipment_id=lk.equipment.get(d.get("equipment") or ""), started_at=d["started_at"],
                             ended_at=d.get("ended_at"), operator_id=d.get("operator_id"))
            db.add(run)
            db.flush()
            for key in [k for k in d if k.startswith("cond:")]:
                defs = lk.conds[key[5:]]
                cdef = next((x for x in defs if x.process_step_id == run.process_step_id), defs[0])
                db.add(ConditionValue(process_run_id=run.id, condition_definition_id=cdef.id, value=d[key]))
        elif t == "measurements":
            lot_id = lk.lot_id(d["lot_no"])
            run_id = None
            step_id = lk.char_step.get(d["_char_id"])
            if d.get("process_step"):
                step_id = lk.steps[d["process_step"]]
            if step_id is not None:
                run_id = db.scalar(select(ProcessRun.id).where(ProcessRun.lot_id == lot_id,
                                                               ProcessRun.process_step_id == step_id)
                                   .order_by(ProcessRun.started_at.desc()).limit(1))
            db.add(Measurement(characteristic_id=d["_char_id"], lot_id=None if run_id else lot_id, process_run_id=run_id,
                               measured_at=d["measured_at"], subgroup_no=d.get("subgroup_no"), value=d["value"]))
        elif t == "defects":
            lot = db.get(Lot, lk.lot_id(d["lot_no"]))
            db.add(DefectJudgment(lot_id=lot.id, defect_type_id=lk.defect_types[d["defect_type"]],
                                  judged_by=user.id if user else None, judged_at=d["judged_at"],
                                  memo=d.get("memo"), attachments=[]))
            lot.status = LotStatus.DEFECT
        n += 1
        if t == "edges":
            db.flush()  # 이후 행의 순환 검사가 DB 를 보도록
    audit.record(db, user, "ingest", t, None, after={"source": source, "rows": n, "invalid": len(res.errors)})
    db.commit()
    return n
