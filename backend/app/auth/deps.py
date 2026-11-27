"""FastAPI 의존성: 현재 사용자, 역할 검사."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.auth.security import read_token
from app.config import get_settings
from app.db import get_db
from app.errors import AppError
from app.models import Role, User

TOKEN_COOKIE = "bif_token"


def _extract_token(request: Request) -> str | None:
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.cookies.get(TOKEN_COOKIE)


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = _extract_token(request)
    if not token:
        raise AppError(401, "로그인이 필요합니다.")
    user_id = read_token(token, get_settings().auth_secret_key)
    if user_id is None:
        raise AppError(401, "로그인 정보가 만료되었거나 올바르지 않습니다. 다시 로그인하세요.")
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise AppError(401, "사용할 수 없는 계정입니다.")
    return user


_ROLE_LABEL = {Role.VIEWER: "조회자", Role.OPERATOR: "실무자", Role.ADMIN: "관리자"}


def require_role(minimum: Role) -> Callable[..., User]:
    def checker(user: User = Depends(get_current_user)) -> User:
        if user.role.level < minimum.level:
            raise AppError(403, f"{_ROLE_LABEL[minimum]} 이상 권한이 필요합니다.")
        return user

    return checker


viewer = require_role(Role.VIEWER)
operator = require_role(Role.OPERATOR)
admin = require_role(Role.ADMIN)
