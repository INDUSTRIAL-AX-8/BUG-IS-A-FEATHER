"""AuditLog 기록. 판정, 원인 확정, 룰/한계/가중치 변경, 마스터 변경은 전부 여기로 남긴다."""

from __future__ import annotations

import enum
from datetime import date, datetime
from typing import Any

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.models import AuditLog, User


def _jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def snapshot(obj: Any) -> dict[str, Any]:
    """ORM 객체의 컬럼 값을 JSON 으로 직렬화 가능한 dict 로."""
    mapper = inspect(obj).mapper
    return {
        col.key: _jsonable(getattr(obj, col.key))
        for col in mapper.column_attrs
        if col.key != "password_hash"
    }


def record(
    db: Session,
    user: User | None,
    action: str,
    entity: str,
    entity_id: Any = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> AuditLog:
    log = AuditLog(
        user_id=user.id if user else None,
        action=action,
        entity=entity,
        entity_id=None if entity_id is None else str(entity_id),
        before=_jsonable(before),
        after=_jsonable(after),
        at=datetime.now(),
    )
    db.add(log)
    return log
