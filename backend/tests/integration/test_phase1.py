"""Phase 1 완료 기준: 샘플 데이터 생성 후 API 로 LOT/계보/측정값 조회, 순환 계보 거부."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select

from app.models import AuditLog


def test_auth_required_and_roles(ctx):
    c = ctx.client
    assert c.get("/api/item-types").status_code == 401
    assert c.get("/api/item-types", headers=ctx.h["viewer"]).status_code == 200
    r = c.post("/api/item-types", json={"name": "X", "category": "RAW"}, headers=ctx.h["viewer"])
    assert r.status_code == 403 and "관리자" in r.json()["message"]
    bad = c.post("/api/auth/login", json={"username": "admin", "password": "nope"})
    assert bad.status_code == 401


def test_master_crud_with_audit(ctx):
    c, h = ctx.client, ctx.h["admin"]
    r = c.post("/api/item-types", json={"name": "수지", "category": "RAW"}, headers=h)
    assert r.status_code == 201
    item_id = r.json()["id"]
    r = c.put(f"/api/item-types/{item_id}", json={"name": "수지-2", "category": "RAW"}, headers=h)
    assert r.json()["name"] == "수지-2"

    dup = c.post("/api/item-types", json={"name": "수지-2", "category": "RAW"}, headers=h)
    assert dup.status_code == 409 and "이미" in dup.json()["message"]

    step = c.post("/api/process-steps", json={"name": "혼합", "seq": 1}, headers=h).json()
    ch = c.post(
        "/api/characteristics",
        json={"name": "점도", "process_step_id": step["id"], "lsl": 1, "usl": 2, "target": 1.5,
              "rule_config": {"2": {"enabled": True, "params": {"count": 7}}}},
        headers=h,
    )
    assert ch.status_code == 201, ch.text

    both = c.post(
        "/api/characteristics",
        json={"name": "잘못", "process_step_id": step["id"], "item_type_id": item_id},
        headers=h,
    )
    assert both.status_code == 422 and "하나에만" in str(both.json())

    with ctx.db() as db:
        n = db.scalar(select(func.count()).select_from(AuditLog))
    assert n >= 4


def test_product_design_nested(ctx):
    c, h = ctx.client, ctx.h["admin"]
    raw = c.post("/api/item-types", json={"name": "A", "category": "RAW"}, headers=h).json()
    out = c.post("/api/item-types", json={"name": "P", "category": "FINISHED"}, headers=h).json()
    step = c.post("/api/process-steps", json={"name": "S", "seq": 1}, headers=h).json()
    r = c.post(
        "/api/product-designs",
        json={"name": "P 표준", "output_item_type_id": out["id"],
              "components": [{"item_type_id": raw["id"], "ratio": 0.6, "tolerance": 0.05}],
              "steps": [{"process_step_id": step["id"], "seq": 1}]},
        headers=h,
    )
    assert r.status_code == 201, r.text
    assert r.json()["components"][0]["ratio"] == 0.6


def test_cycle_rejected(ctx):
    c, h = ctx.client, ctx.h["operator"]
    admin = ctx.h["admin"]
    it = c.post("/api/item-types", json={"name": "T", "category": "SEMI"}, headers=admin).json()
    for no in ("L1", "L2", "L3"):
        r = c.post("/api/lots", json={"lot_no": no, "item_type_id": it["id"],
                                      "created_at": datetime(2026, 1, 1).isoformat()}, headers=h)
        assert r.status_code == 201
    assert c.post("/api/genealogy/edges", json={"parent_lot_no": "L1", "child_lot_no": "L2"}, headers=h).status_code == 201
    assert c.post("/api/genealogy/edges", json={"parent_lot_no": "L2", "child_lot_no": "L3"}, headers=h).status_code == 201
    r = c.post("/api/genealogy/edges", json={"parent_lot_no": "L3", "child_lot_no": "L1"}, headers=h)
    assert r.status_code == 409 and "순환" in r.json()["message"]
    r = c.post("/api/genealogy/edges", json={"parent_lot_no": "L1", "child_lot_no": "L1"}, headers=h)
    assert r.status_code == 409


def test_sample_lot_genealogy_measurements(sample_ctx):
    c, h, s = sample_ctx.client, sample_ctx.h["viewer"], sample_ctx.scenario
    assert s.counts["불량 판정 수"] > 0
    product_no = s.planted_defect_lot_nos[0]

    lot = c.get(f"/api/lots/{product_no}", headers=h).json()
    assert lot["status"] == "DEFECT" and lot["parent_count"] == 1

    g = c.get(f"/api/lots/{product_no}/genealogy?direction=up&depth=5", headers=h).json()
    nos = {l["lot_no"] for l in g["lots"]}
    assert s.planted_raw_lot_no in nos  # 원자재까지 역추적
    assert len(g["edges"]) >= 5  # 병합: 배치에 A 2개 + B + C

    down = c.get(f"/api/lots/{s.planted_raw_lot_no}/genealogy?direction=down&depth=3", headers=h).json()
    assert product_no in {l["lot_no"] for l in down["lots"]}  # 분할된 제품까지 정방향

    runs = c.get(f"/api/lots/{product_no}/runs", headers=h).json()
    assert {r["process_step_name"] for r in runs} >= {"충전", "최종검사"}

    meas = c.get(f"/api/lots/{product_no}/measurements", headers=h).json()
    assert len(meas) == 6  # 중량 5 + 강도 1

    m = c.get(f"/api/measurements?characteristic_id={s.trend_characteristic_id}", headers=h).json()
    assert len(m) >= 30

    lots = c.get("/api/lots?q=RA-&limit=5", headers=h).json()
    assert len(lots) == 5 and all(l["lot_no"].startswith("RA-") for l in lots)
    assert c.get("/api/lots/없는LOT", headers=h).status_code == 404


def test_sample_is_deterministic(sample_ctx):
    from tests.conftest import _make_session_factory
    from app.auth.provider import create_user
    from app.models import Role
    from app.sample.generator import generate

    factory = _make_session_factory()
    with factory() as db:
        create_user(db, "admin", "admin1234", Role.ADMIN)
        db.commit()
        again = generate(db, seed=42)
    assert again.counts == sample_ctx.scenario.counts
    assert again.planted_defect_lot_nos == sample_ctx.scenario.planted_defect_lot_nos


def test_settings_defaults_and_update(ctx):
    c = ctx.client
    w = c.get("/api/settings/cause_weights", headers=ctx.h["viewer"]).json()
    assert "lift" in w
    r = c.put("/api/settings/cause_weights", json={"lift": 2.0}, headers=ctx.h["admin"])
    assert r.status_code == 200 and r.json()["lift"] == 2.0 and "spc" in r.json()
    bad = c.put("/api/settings/cause_weights", json={"nope": 1}, headers=ctx.h["admin"])
    assert bad.status_code == 422


def test_alembic_migration_matches_models(tmp_path):
    from alembic import command
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from app.cli import alembic_config
    from app.db import Base, make_engine

    url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    command.upgrade(alembic_config(url), "head")
    engine = make_engine(url)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    engine.dispose()
    assert diff == [], diff


def test_spa_fallback_and_missing_files(tmp_path, monkeypatch):
    """화면 경로는 index.html(캐시 안 함), 없는 파일(/sw.js 등)은 404 — 남은 서비스 워커가 스스로 해제되도록."""
    from app.config import get_settings
    from app.main import create_app
    from tests.conftest import open_client

    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html>app</html>", encoding="utf-8")
    (tmp_path / "assets" / "a.js").write_text("x", encoding="utf-8")
    monkeypatch.setattr(get_settings(), "server_frontend_dist", str(tmp_path))
    c = open_client(create_app(start_scheduler=False))
    page = c.get("/genealogy")
    assert page.status_code == 200 and "app" in page.text and page.headers["cache-control"] == "no-cache"
    assert c.get("/assets/a.js").status_code == 200
    assert c.get("/sw.js").status_code == 404
    assert c.get("/service-worker.js").status_code == 404
    assert c.get("/api/nope").status_code == 404
