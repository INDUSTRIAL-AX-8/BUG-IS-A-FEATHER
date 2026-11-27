"""데이터 업로드(컬럼 매핑) · REST 수집 · 매핑 템플릿. 업로드와 수집은 같은 검증 로직을 쓴다."""

from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import operator, viewer
from app.config import PROJECT_DIR
from app.db import get_db
from app.errors import AppError, not_found
from app.ingest.files import apply_mapping, read_table
from app.ingest.validate import TARGETS, ValidationResult, commit_rows, validate_rows
from app.models import ConditionDefinition, MappingTemplate, User
from app.services import audit

router = APIRouter(tags=["데이터 업로드/수집"])

UPLOAD_DIR = PROJECT_DIR / "data" / "uploads"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_ROWS = 200_000


def _target(name: str) -> dict:
    if name not in TARGETS:
        raise AppError(422, f"알 수 없는 대상입니다: {name} (가능: {', '.join(TARGETS)})")
    return TARGETS[name]


@router.get("/ingest/targets")
def targets(db: Session = Depends(get_db), _=Depends(viewer)):
    cond_names = sorted({n for n in db.scalars(select(ConditionDefinition.name))})
    return [
        {"target": k, "label": v["label"],
         "fields": [{"name": f.name, "label": f.label, "kind": f.kind, "required": f.required} for f in v["fields"]],
         "dynamic": [{"name": f"cond:{n}", "label": f"조건: {n}"} for n in cond_names] if v.get("dynamic") == "cond" else []}
        for k, v in TARGETS.items()
    ]


# ------------------------------------------------------------------ 파일 업로드

def _upload_path(upload_id: str) -> Path:
    if not upload_id.isalnum():
        raise not_found("업로드 파일")
    matches = list(UPLOAD_DIR.glob(f"{upload_id}.*"))
    files = [m for m in matches if m.suffix not in (".json", ".csv") or m.name.endswith(".src.csv")]
    if not files:
        raise not_found("업로드 파일")
    return files[0]


@router.post("/upload")
async def upload(file: UploadFile = File(...), _: User = Depends(operator)):
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise AppError(413, "파일은 50MB 이하만 올릴 수 있습니다.")
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in (".csv", ".txt", ".xlsx", ".xlsm"):
        raise AppError(422, "CSV(.csv) 또는 Excel(.xlsx) 파일만 올릴 수 있습니다.")
    upload_id = uuid.uuid4().hex
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    # 원본 CSV 와 오류 CSV 이름이 겹치지 않게 원본 CSV 는 .src.csv 로 저장
    path = UPLOAD_DIR / (f"{upload_id}.src.csv" if suffix in (".csv", ".txt") else f"{upload_id}{suffix}")
    path.write_bytes(data)
    try:
        columns, rows = read_table(path)
    except ValueError as exc:
        path.unlink(missing_ok=True)
        raise AppError(422, str(exc)) from exc
    if len(rows) > MAX_ROWS:
        path.unlink(missing_ok=True)
        raise AppError(413, f"한 번에 {MAX_ROWS:,}행까지 올릴 수 있습니다. 파일을 나눠 주세요.")
    return {"upload_id": upload_id, "filename": file.filename, "columns": columns, "row_count": len(rows),
            "sample": rows[:5]}


class MappingIn(BaseModel):
    target: str
    mapping: dict[str, str] = Field(description="{시스템 필드: 원본 열}. 조건값은 'cond:<조건 이름>' 필드")


def _validate_upload(db: Session, upload_id: str, body: MappingIn) -> tuple[ValidationResult, list[dict]]:
    spec = _target(body.target)
    missing = [f.label for f in spec["fields"] if f.required and not body.mapping.get(f.name)]
    if missing:
        raise AppError(422, f"필수 필드를 매핑하세요: {', '.join(missing)}")
    columns, rows = read_table(_upload_path(upload_id))
    unknown = sorted({c for c in body.mapping.values() if c and c not in columns})
    if unknown:
        raise AppError(422, f"파일에 없는 열입니다: {', '.join(unknown)}")
    mapped = apply_mapping(rows, body.mapping)
    res = validate_rows(db, body.target, mapped, start_row=2)  # 엑셀 기준 행 번호 (1행 = 헤더)
    _write_errors(upload_id, res, rows, columns)
    return res, rows


def _write_errors(upload_id: str, res: ValidationResult, rows: list[dict], columns: list[str]) -> None:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["행 번호", "오류"] + columns)
    for e in res.errors:
        src = rows[e.row - 2]
        w.writerow([e.row, " / ".join(e.messages)] + [src.get(c, "") for c in columns])
    (UPLOAD_DIR / f"{upload_id}.errors.csv").write_text(buf.getvalue(), encoding="utf-8-sig")


@router.post("/upload/{upload_id}/preview")
def preview(upload_id: str, body: MappingIn, db: Session = Depends(get_db), _: User = Depends(operator)):
    res, _ = _validate_upload(db, upload_id, body)
    return {**res.summary(preview=20), "errors_url": f"/api/upload/{upload_id}/errors.csv" if res.errors else None}


@router.post("/upload/{upload_id}/commit")
def commit(upload_id: str, body: MappingIn, db: Session = Depends(get_db), user: User = Depends(operator)):
    res, _ = _validate_upload(db, upload_id, body)
    n = commit_rows(db, res, user, source=f"upload:{upload_id}")
    _after_commit(db, body.target)
    return {"inserted": n, "invalid": len(res.errors),
            "errors_url": f"/api/upload/{upload_id}/errors.csv" if res.errors else None}


@router.get("/upload/{upload_id}/errors.csv")
def errors_csv(upload_id: str, _=Depends(viewer)):
    if not upload_id.isalnum():
        raise not_found("오류 파일")
    path = UPLOAD_DIR / f"{upload_id}.errors.csv"
    if not path.is_file():
        raise not_found("오류 파일")
    return Response(path.read_bytes(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="errors_{upload_id[:8]}.csv"'})


def _after_commit(db: Session, target: str) -> None:
    if target == "measurements":
        from app.services.spc_service import recompute_all

        recompute_all(db)


# ------------------------------------------------------------------ REST 수집 (MES/LIMS)

class IngestIn(BaseModel):
    rows: list[dict[str, Any]] = Field(min_length=1, max_length=50_000)
    dry_run: bool = False


@router.post("/ingest/{target}")
def ingest(target: str, body: IngestIn, db: Session = Depends(get_db), user: User = Depends(operator)):
    _target(target)
    res = validate_rows(db, target, body.rows, start_row=1)
    out = {"target": target, "total": res.total, "valid": len(res.valid), "invalid": len(res.errors),
           "errors": [{"row": e.row, "messages": e.messages} for e in res.errors[:500]], "dry_run": body.dry_run}
    if not body.dry_run:
        out["inserted"] = commit_rows(db, res, user, source="api")
        _after_commit(db, target)
    return out


# ------------------------------------------------------------------ 매핑 템플릿

class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    target: str
    column_map: dict[str, str]


def _tpl_out(t: MappingTemplate) -> dict:
    return {"id": t.id, "name": t.name, "target": t.target, "column_map": t.column_map, "created_at": t.created_at}


@router.get("/mapping-templates")
def list_templates(target: str | None = None, db: Session = Depends(get_db), _=Depends(viewer)):
    stmt = select(MappingTemplate).order_by(MappingTemplate.name)
    if target:
        stmt = stmt.where(MappingTemplate.target == target)
    return [_tpl_out(t) for t in db.scalars(stmt)]


@router.post("/mapping-templates", status_code=201)
def save_template(body: TemplateIn, db: Session = Depends(get_db), user: User = Depends(operator)):
    _target(body.target)
    t = db.scalar(select(MappingTemplate).where(MappingTemplate.name == body.name))
    before = None
    if t is None:
        t = MappingTemplate(name=body.name, target=body.target, column_map=body.column_map, created_at=datetime.now())
        db.add(t)
    else:
        before = audit.snapshot(t)
        t.target, t.column_map = body.target, body.column_map
    db.flush()
    audit.record(db, user, "update" if before else "create", "mapping_template", t.id, before=before,
                 after=json.loads(json.dumps(audit.snapshot(t), default=str)))
    db.commit()
    return _tpl_out(t)


@router.delete("/mapping-templates/{template_id}", status_code=204)
def delete_template(template_id: int, db: Session = Depends(get_db), user: User = Depends(operator)):
    t = db.get(MappingTemplate, template_id)
    if t is None:
        raise not_found("매핑 템플릿")
    audit.record(db, user, "delete", "mapping_template", t.id, before=audit.snapshot(t))
    db.delete(t)
    db.commit()
