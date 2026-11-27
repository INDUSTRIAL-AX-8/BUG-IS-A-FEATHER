"""인증 계층. 지금은 로컬 계정만 지원하며, 외부 인증(LDAP/SSO 등)은 AuthProvider 를 구현해 교체한다."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.security import hash_password, verify_password
from app.models import Role, User


class AuthProvider(Protocol):
    def authenticate(self, db: Session, username: str, password: str) -> User | None: ...


class LocalAuthProvider:
    def authenticate(self, db: Session, username: str, password: str) -> User | None:
        user = db.scalar(select(User).where(User.username == username))
        if user is None or not user.is_active:
            return None
        if not verify_password(password, user.password_hash):
            return None
        return user


_provider: AuthProvider = LocalAuthProvider()


def get_provider() -> AuthProvider:
    return _provider


def set_provider(provider: AuthProvider) -> None:
    global _provider
    _provider = provider


def create_user(
    db: Session, username: str, password: str, role: Role, display_name: str | None = None
) -> User:
    user = User(
        username=username,
        display_name=display_name,
        password_hash=hash_password(password),
        role=role,
        is_active=True,
        created_at=datetime.now(),
    )
    db.add(user)
    db.flush()
    return user


def ensure_initial_admin(db: Session, username: str, password: str) -> User | None:
    """사용자가 한 명도 없을 때만 초기 관리자 계정을 만든다."""
    if db.scalar(select(User.id).limit(1)) is not None:
        return None
    user = create_user(db, username, password, Role.ADMIN, display_name="관리자")
    db.commit()
    return user
