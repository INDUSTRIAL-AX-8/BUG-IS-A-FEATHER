from __future__ import annotations

import os

# 앱 import 전에 실제 DB 파일을 건드리지 않도록 설정
os.environ.setdefault("BIF_DATABASE_URL", "sqlite://")
os.environ.setdefault("BIF_SCHEDULER_ENABLED", "false")
os.environ.setdefault("BIF_AUTH_SECRET_KEY", "test-secret")
os.environ.setdefault("BIF_LLM_OLLAMA_URL", "http://127.0.0.1:9")  # 연결 불가 주소

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.auth.provider import create_user
from app.db import Base, get_db, make_engine
from app.main import create_app
from app.models import Role


def _make_session_factory():
    from sqlalchemy import create_engine, event

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _client_for(factory) -> TestClient:
    app = create_app(start_scheduler=False)

    def _get_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _get_db
    app.state.session_factory = factory
    client = TestClient(app)
    # 요청마다 이벤트 루프를 새로 만들지 않도록 포털을 열어 둔다 (Windows 소켓 생성 최소화)
    client.__enter__()
    _OPEN_CLIENTS.append(client)
    return client


_OPEN_CLIENTS: list[TestClient] = []


@pytest.fixture(autouse=True, scope="session")
def _close_clients():
    yield
    for c in _OPEN_CLIENTS:
        try:
            c.__exit__(None, None, None)
        except Exception:
            pass


def _login(client: TestClient, username: str, password: str) -> dict[str, str]:
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    client.cookies.clear()  # 헤더 기반으로만 인증하도록
    return {"Authorization": f"Bearer {r.json()['token']}"}


class Ctx:
    def __init__(self, factory, client, headers, scenario=None):
        self.factory = factory
        self.client = client
        self.h = headers
        self.scenario = scenario

    def db(self):
        return self.factory()


@pytest.fixture()
def ctx() -> Ctx:
    """빈 DB + 역할별 사용자."""
    factory = _make_session_factory()
    with factory() as db:
        create_user(db, "admin", "admin1234", Role.ADMIN)
        create_user(db, "op", "op123456", Role.OPERATOR)
        create_user(db, "view", "view1234", Role.VIEWER)
        db.commit()
    client = _client_for(factory)
    headers = {
        "admin": _login(client, "admin", "admin1234"),
        "operator": _login(client, "op", "op123456"),
        "viewer": _login(client, "view", "view1234"),
    }
    return Ctx(factory, client, headers)


@pytest.fixture(scope="session")
def sample_ctx() -> Ctx:
    """샘플 시나리오가 들어 있는 DB (세션 공유 — 읽기 위주 테스트용)."""
    from app.sample.generator import generate

    factory = _make_session_factory()
    with factory() as db:
        create_user(db, "admin", "admin1234", Role.ADMIN)
        db.commit()
        scenario = generate(db, seed=42)
    client = _client_for(factory)
    headers = {
        "admin": _login(client, "admin", "admin1234"),
        "operator": _login(client, "operator", "operator1234"),
        "viewer": _login(client, "viewer", "viewer1234"),
    }
    return Ctx(factory, client, headers, scenario)


@pytest.fixture()
def fresh_sample_ctx() -> Ctx:
    """샘플 시나리오 DB (테스트마다 새로 생성 — 데이터를 바꾸는 테스트용)."""
    from app.sample.generator import generate

    factory = _make_session_factory()
    with factory() as db:
        create_user(db, "admin", "admin1234", Role.ADMIN)
        db.commit()
        scenario = generate(db, seed=42)
    client = _client_for(factory)
    headers = {
        "admin": _login(client, "admin", "admin1234"),
        "operator": _login(client, "operator", "operator1234"),
        "viewer": _login(client, "viewer", "viewer1234"),
    }
    return Ctx(factory, client, headers, scenario)


__all__ = ["Ctx", "make_engine"]
