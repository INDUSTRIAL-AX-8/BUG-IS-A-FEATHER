"""로그인, 사용자, 설정 API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import TOKEN_COOKIE, admin, get_current_user, viewer
from app.auth.provider import create_user, get_provider
from app.auth.security import hash_password, issue_token
from app.config import get_settings
from app.db import get_db
from app.errors import AppError, not_found
from app.models import Role, User
from app.schemas.master import ORM
from app.services import audit
from app.services.settings_service import all_settings, get_setting, put_setting

router = APIRouter(tags=["인증/설정"])


class LoginIn(BaseModel):
    username: str
    password: str


class UserOut(ORM):
    id: int
    username: str
    display_name: str | None
    role: Role
    is_active: bool
    created_at: datetime


class LoginOut(BaseModel):
    token: str
    user: UserOut


class UserIn(BaseModel):
    username: str = Field(min_length=2, max_length=100)
    password: str = Field(min_length=6)
    display_name: str | None = None
    role: Role = Role.VIEWER


class UserUpdate(BaseModel):
    display_name: str | None = None
    role: Role | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=6)


@router.post("/auth/login", response_model=LoginOut)
def login(body: LoginIn, response: Response, db: Session = Depends(get_db)):
    user = get_provider().authenticate(db, body.username, body.password)
    if user is None:
        raise AppError(401, "아이디 또는 비밀번호가 올바르지 않습니다.")
    s = get_settings()
    token = issue_token(user.id, s.auth_secret_key, s.auth_token_ttl_minutes)
    response.set_cookie(
        TOKEN_COOKIE, token, httponly=True, samesite="lax", max_age=s.auth_token_ttl_minutes * 60
    )
    return LoginOut(token=token, user=UserOut.model_validate(user))


@router.post("/auth/logout", status_code=204)
def logout(response: Response):
    response.delete_cookie(TOKEN_COOKIE)


@router.get("/auth/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


@router.get("/users", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), _=Depends(admin)):
    return db.scalars(select(User).order_by(User.username)).all()


@router.post("/users", response_model=UserOut, status_code=201)
def add_user(body: UserIn, db: Session = Depends(get_db), user: User = Depends(admin)):
    if db.scalar(select(User.id).where(User.username == body.username)):
        raise AppError(409, f"이미 있는 아이디입니다: {body.username}")
    new = create_user(db, body.username, body.password, body.role, body.display_name)
    audit.record(db, user, "create", "user", new.id, after=audit.snapshot(new))
    db.commit()
    return new


@router.put("/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UserUpdate, db: Session = Depends(get_db), user: User = Depends(admin)):
    target = db.get(User, user_id)
    if target is None:
        raise not_found("사용자")
    before = audit.snapshot(target)
    if body.display_name is not None:
        target.display_name = body.display_name
    if body.role is not None:
        if target.id == user.id and body.role != Role.ADMIN:
            raise AppError(409, "자기 자신의 관리자 권한은 해제할 수 없습니다.")
        target.role = body.role
    if body.is_active is not None:
        if target.id == user.id and not body.is_active:
            raise AppError(409, "자기 자신은 비활성화할 수 없습니다.")
        target.is_active = body.is_active
    if body.password:
        target.password_hash = hash_password(body.password)
    db.flush()
    after = audit.snapshot(target)
    if body.password:
        after["password_changed"] = True
    audit.record(db, user, "update", "user", target.id, before=before, after=after)
    db.commit()
    return target


@router.get("/settings")
def read_settings(db: Session = Depends(get_db), _=Depends(viewer)) -> dict[str, Any]:
    return all_settings(db)


@router.get("/settings/{key}")
def read_setting(key: str, db: Session = Depends(get_db), _=Depends(viewer)) -> Any:
    return get_setting(db, key)


@router.put("/settings/{key}")
def write_setting(key: str, value: Any = Body(...), db: Session = Depends(get_db), user: User = Depends(admin)) -> Any:
    get_setting(db, key)  # 존재 확인
    return put_setting(db, key, value, user)
