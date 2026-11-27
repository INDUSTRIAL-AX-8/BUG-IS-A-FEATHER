"""DB 업무 설정 조회/변경. 없는 키는 defaults 로 채운다."""

from __future__ import annotations

import copy
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.defaults import SETTING_DEFAULTS
from app.errors import AppError
from app.models import Setting, User
from app.services import audit


def _merge(default: Any, override: Any) -> Any:
    if isinstance(default, dict) and isinstance(override, dict):
        merged = copy.deepcopy(default)
        for k, v in override.items():
            merged[k] = _merge(default.get(k), v) if k in default else copy.deepcopy(v)
        return merged
    return copy.deepcopy(override) if override is not None else copy.deepcopy(default)


def get_setting(db: Session, key: str) -> Any:
    if key not in SETTING_DEFAULTS:
        raise AppError(404, f"알 수 없는 설정 항목입니다: {key}")
    row = db.get(Setting, key)
    return _merge(SETTING_DEFAULTS[key], row.value if row else None)


def all_settings(db: Session) -> dict[str, Any]:
    return {key: get_setting(db, key) for key in SETTING_DEFAULTS}


def put_setting(db: Session, key: str, value: Any, user: User | None) -> Any:
    before = get_setting(db, key)
    default = SETTING_DEFAULTS[key]
    if isinstance(default, dict) and not isinstance(value, dict):
        raise AppError(422, "설정 값은 항목별 키-값 형태여야 합니다.")
    if isinstance(default, dict):
        unknown = set(value) - set(default)
        if unknown and key not in ("nelson_rules", "dataset_notes", "process_lines", "capacity"):
            raise AppError(422, f"알 수 없는 설정 키입니다: {', '.join(sorted(unknown))}")
    if key == "terminology" and value.get("preset", "general") not in ("general", "injection", "diecasting"):
        raise AppError(422, "용어 프리셋은 general, injection, diecasting 중 하나여야 합니다.")
    if key == "nelson_rules":
        from app.core.spc.nelson import validate_config

        errors = validate_config(value)
        if errors:
            raise AppError(422, "넬슨룰 설정이 올바르지 않습니다.", errors)
    row = db.get(Setting, key)
    if row is None:
        row = Setting(key=key, value=value, updated_at=datetime.now())
        db.add(row)
    else:
        row.value = value
        row.updated_at = datetime.now()
    after = _merge(default, value)
    audit.record(db, user, "update", "setting", key, before={"value": before}, after={"value": after})
    db.commit()
    return after
