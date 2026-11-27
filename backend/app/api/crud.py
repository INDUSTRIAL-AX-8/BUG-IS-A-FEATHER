"""단순 마스터용 CRUD 라우터 생성기. 조회는 조회자 이상, 변경은 관리자 + AuditLog.

주의: 클로저 안의 타입을 FastAPI 가 런타임에 읽어야 하므로 `from __future__ import annotations` 를 쓰지 않는다.
"""


from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import admin, viewer
from app.db import get_db
from app.errors import not_found
from app.models import User
from app.services import audit


def make_crud_router(
    *,
    model: type,
    schema_in: type[BaseModel],
    schema_out: type[BaseModel],
    prefix: str,
    label: str,
    entity: str,
    order_by: Any = None,
    validate: Any = None,
) -> APIRouter:
    """validate(db, data: dict, obj | None) 는 FK 존재 확인 등 추가 검증 훅."""
    router = APIRouter(prefix=prefix, tags=[label])

    @router.get("", response_model=list[schema_out])
    def list_items(active_only: bool = False, db: Session = Depends(get_db), _=Depends(viewer)):
        stmt = select(model)
        if active_only and hasattr(model, "is_active"):
            stmt = stmt.where(model.is_active.is_(True))
        stmt = stmt.order_by(order_by if order_by is not None else model.id)
        return db.scalars(stmt).all()

    @router.get("/{item_id}", response_model=schema_out)
    def get_item(item_id: int, db: Session = Depends(get_db), _=Depends(viewer)):
        obj = db.get(model, item_id)
        if obj is None:
            raise not_found(label)
        return obj

    @router.post("", response_model=schema_out, status_code=201)
    def create_item(body: schema_in, db: Session = Depends(get_db), user: User = Depends(admin)):  # type: ignore[valid-type]
        data = body.model_dump(mode="json")
        if validate:
            validate(db, data, None)
        obj = model(**body.model_dump())
        db.add(obj)
        db.flush()
        audit.record(db, user, "create", entity, obj.id, after=audit.snapshot(obj))
        db.commit()
        return obj

    @router.put("/{item_id}", response_model=schema_out)
    def update_item(
        item_id: int, body: schema_in, db: Session = Depends(get_db), user: User = Depends(admin)  # type: ignore[valid-type]
    ):
        obj = db.get(model, item_id)
        if obj is None:
            raise not_found(label)
        data = body.model_dump(mode="json")
        if validate:
            validate(db, data, obj)
        before = audit.snapshot(obj)
        for key, value in body.model_dump().items():
            setattr(obj, key, value)
        db.flush()
        audit.record(db, user, "update", entity, obj.id, before=before, after=audit.snapshot(obj))
        db.commit()
        return obj

    @router.delete("/{item_id}", status_code=204)
    def delete_item(item_id: int, db: Session = Depends(get_db), user: User = Depends(admin)):
        obj = db.get(model, item_id)
        if obj is None:
            raise not_found(label)
        before = audit.snapshot(obj)
        db.delete(obj)
        db.flush()
        audit.record(db, user, "delete", entity, item_id, before=before)
        db.commit()

    return router


def require_exists(db: Session, model: type, item_id: int | None, label: str) -> None:
    if item_id is not None and db.get(model, item_id) is None:
        raise not_found(label)
