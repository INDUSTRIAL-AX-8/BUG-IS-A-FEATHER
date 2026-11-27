"""인증 기본값 보안: 공개된 서명 키로 토큰 위조 불가, 공개된 초기 비밀번호는 첫 로그인 때 변경 강제."""

from __future__ import annotations

import pytest

from app import config
from app.auth.security import issue_token, read_token
from app.config import INSECURE_SECRET_KEYS, get_secret_key, get_settings

NEW_PASSWORD = "Line3-Shift-Change!"


@pytest.fixture()
def enforce(monkeypatch):
    """운영 기본값: 공개된 초기 비밀번호 변경 강제."""
    monkeypatch.setattr(get_settings(), "auth_allow_default_passwords", False)


@pytest.fixture()
def placeholder_secret(monkeypatch, tmp_path):
    """설정의 secret_key 가 저장소에 공개된 기본값인 서버 (config.toml 을 안 만들었거나 예시를 그대로 복사)."""
    key_file = tmp_path / "data" / "secret.key"
    monkeypatch.setattr(get_settings(), "auth_secret_key", "change-me-in-config")
    monkeypatch.setattr(get_settings(), "auth_secret_file", str(key_file))
    get_secret_key.cache_clear()
    yield key_file
    get_secret_key.cache_clear()


def test_public_placeholder_secret_is_replaced_by_generated_key(placeholder_secret):
    key = get_secret_key()
    assert key not in INSECURE_SECRET_KEYS and len(key) >= 32
    assert placeholder_secret.read_text(encoding="utf-8") == key
    get_secret_key.cache_clear()
    assert get_secret_key() == key  # 재시작해도 같은 키 (로그인 유지)


def test_token_forged_with_public_secret_is_rejected(ctx, placeholder_secret):
    for public in sorted(INSECURE_SECRET_KEYS - {""}):
        forged = issue_token(1, public, 60)
        r = ctx.client.get("/api/users", headers={"Authorization": f"Bearer {forged}"})
        assert r.status_code == 401, public
    # 서버가 만든 키로 서명한 토큰은 통과
    assert read_token(issue_token(1, get_secret_key(), 60), get_secret_key()) == 1


def test_configured_secret_is_used_as_is(monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "auth_secret_key", "site-specific-long-random-value")
    monkeypatch.setattr(get_settings(), "auth_secret_file", str(tmp_path / "secret.key"))
    get_secret_key.cache_clear()
    try:
        assert config.get_secret_key() == "site-specific-long-random-value"
        assert not (tmp_path / "secret.key").exists()
    finally:
        get_secret_key.cache_clear()


def test_default_password_login_can_only_change_password(ctx, enforce):
    c = ctx.client
    r = c.post("/api/auth/login", json={"username": "admin", "password": "admin1234"})
    assert r.status_code == 200 and r.json()["must_change_password"] is True
    c.cookies.clear()
    limited = {"Authorization": f"Bearer {r.json()['token']}"}

    blocked = c.get("/api/users", headers=limited)
    assert blocked.status_code == 403 and "비밀번호" in blocked.json()["message"]
    assert c.get("/api/lots", headers=limited).status_code == 403
    assert c.get("/api/auth/me", headers=limited).json()["must_change_password"] is True

    def change(current, new):
        return c.post("/api/auth/password", headers=limited, json={"current_password": current, "new_password": new})

    assert change("wrong-password", NEW_PASSWORD).status_code == 401
    assert change("admin1234", "operator1234").status_code == 422  # 다른 공개 초기 비밀번호로는 못 바꿈
    assert change("admin1234", "short").status_code == 422
    ok = change("admin1234", NEW_PASSWORD)
    assert ok.status_code == 200 and ok.json()["must_change_password"] is False
    c.cookies.clear()

    full = {"Authorization": f"Bearer {ok.json()['token']}"}
    assert c.get("/api/users", headers=full).status_code == 200
    assert c.get("/api/users", headers=limited).status_code == 403  # 예전 토큰은 계속 제한
    assert c.post("/api/auth/login", json={"username": "admin", "password": "admin1234"}).status_code == 401
    again = c.post("/api/auth/login", json={"username": "admin", "password": NEW_PASSWORD})
    assert again.status_code == 200 and again.json()["must_change_password"] is False
    c.cookies.clear()

    with ctx.db() as db:
        from sqlalchemy import select

        from app.models import AuditLog

        logs = db.scalars(select(AuditLog).where(AuditLog.entity == "user", AuditLog.action == "update")).all()
        assert any((log.after or {}).get("by") == "self" for log in logs)


def test_non_default_password_is_not_forced(ctx, enforce):
    r = ctx.client.post("/api/auth/login", json={"username": "op", "password": "op123456"})
    assert r.status_code == 200 and r.json()["must_change_password"] is False
    ctx.client.cookies.clear()
    assert ctx.client.get("/api/lots", headers={"Authorization": f"Bearer {r.json()['token']}"}).status_code == 200


def test_allow_default_passwords_setting_skips_forced_change(ctx):
    """데모·시연용: allow_default_passwords = true 면 공개 초기 비밀번호를 그대로 쓴다 (테스트 기본 환경)."""
    assert get_settings().auth_allow_default_passwords is True
    r = ctx.client.post("/api/auth/login", json={"username": "admin", "password": "admin1234"})
    assert r.json()["must_change_password"] is False
    ctx.client.cookies.clear()
