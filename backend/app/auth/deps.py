"""FastAPI 의존성: 현재 사용자, 역할 검사."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.auth.security import read_token_claims
from app.config import get_secret_key
from app.db import get_db
from app.errors import AppError
from app.models import Role, User

TOKEN_COOKIE = "bif_token"
# 비밀번호 변경이 필요한 토큰으로도 쓸 수 있는 경로 (그 밖의 API 는 403)
_PASSWORD_CHANGE_PATHS = ("/auth/me", "/auth/password", "/auth/logout")


def _extract_token(request: Request) -> str | None:
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.cookies.get(TOKEN_COOKIE)


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = _extract_token(request)
    if not token:
        raise AppError(401, "로그인이 필요합니다.")
    claims = read_token_claims(token, get_secret_key())
    if claims is None:
        raise AppError(401, "로그인 정보가 만료되었거나 올바르지 않습니다. 다시 로그인하세요.")
    user = db.get(User, claims["sub"])
    if user is None or not user.is_active:
        raise AppError(401, "사용할 수 없는 계정입니다.")
    must_change = bool(claims.get("chg"))
    request.state.must_change_password = must_change
    if must_change and not request.url.path.endswith(_PASSWORD_CHANGE_PATHS):
        raise AppError(403, "초기 비밀번호를 사용 중입니다. 비밀번호를 바꾼 뒤 이용하세요.")
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
