"""실시간 수집 시뮬레이터: 학습된 지식으로 '이상 → 경보 → 정비 → 회복'을 재현하고 SSE 로 알린다."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.auth.provider import create_user
from app.live import simulator as live
from app.models import EquipmentEvent, Lot, Role
from app.sample.process_log import import_process_log, load_mapping
from app.services.driver_service import analyze_equipment
from tests.conftest import Ctx, _client_for, _login, _make_session_factory

SAMPLE = Path(__file__).resolve().parents[3] / "samples" / "injection_m3"


@pytest.fixture()
def lv():
    factory = _make_session_factory()
    with factory() as db:
        create_user(db, "admin", "admin1234", Role.ADMIN)
        db.commit()
        s = import_process_log(db, SAMPLE / "hourly_2026-03-04_06.csv", load_mapping(SAMPLE / "mapping.json"))
        analyze_equipment(db, s.equipment_id, apply_learned=True)
    sim = live.LiveSimulator(factory)
    live.set_simulator(sim)
    client = _client_for(factory)
    h = {"viewer": _login(client, "viewer", "viewer1234"), "operator": _login(client, "operator", "operator1234")}
    yield Ctx(factory, client, h, s), sim
    live.set_simulator(None)


def _manual(sim, eq_id):
    sim.start(eq_id, "drift", interval_sec=60, seed=1)  # 스레드는 60초 대기 → 바로 멈추고 수동 tick
    sim.stop()
    sim.state.running = True


def test_drift_scenario_uses_learned_primary_and_recovers(lv):
    ctx, sim = lv
    q = sim.subscribe()
    _manual(sim, ctx.scenario.equipment_id)
    assert sim.status()["drift_condition"] == "금형 온도"
    recs = [sim.tick() for _ in range(16)]
    records = [r for r in recs if r["type"] == "record"]
    events = [r for r in recs if r["type"] == "equipment_event"]
    molds = [r["values"]["금형 온도"] for r in records]
    assert molds[0] < 57 and max(molds) > 57  # 학습 경계를 넘어감
    assert all(r["values"]["사출 압력 설정"] == 75 for r in records)  # 설정값은 고정
    over = [r for r in records if r["out_of_range"]]
    normal = [r for r in records[:4]]
    assert sum(r["rate"] for r in over) / len(over) > 2 * sum(r["rate"] for r in normal) / len(normal)
    assert events and events[0]["kind"] == "STOP"
    after = records[-1]
    assert after["values"]["금형 온도"] < 57 and not after["out_of_range"]  # 정비 후 회복
    msgs = []
    while not q.empty():
        msgs.append(q.get_nowait())
    assert any(m["type"] == "alarm" for m in msgs) and any(m["type"] == "record" for m in msgs)
    with ctx.db() as db:
        n_events = db.scalar(select(func.count()).select_from(EquipmentEvent)) - 1  # 원본 정지 1건 제외
        # 시작 직후 스레드가 한 틱을 먼저 만든다 → 전체 틱 수 기준으로 확인
        assert db.scalar(select(func.count()).select_from(Lot)) == 23 + sim.state.ticks - n_events
        assert n_events == len(events)


def test_live_api_and_stream(lv):
    ctx, sim = lv
    c, h = ctx.client, ctx.h
    assert c.post("/api/live/start", headers=h["viewer"], json={}).status_code == 403
    assert c.post("/api/live/start", headers=h["operator"], json={"scenario": "nope"}).status_code == 422
    r = c.post("/api/live/start", headers=h["operator"], json={"interval_sec": 60})
    assert r.status_code == 200 and r.json()["running"] and r.json()["equipment"] == "사출기 3호"
    assert c.get("/api/live/status", headers=h["viewer"]).json()["running"]
    s = c.get("/api/live/stream?limit=1", headers=h["viewer"])
    assert s.status_code == 200 and "event: status" in s.text and "사출기 3호" in s.text
    assert c.post("/api/live/maintenance", headers=h["operator"]).json()["ok"]
    assert c.post("/api/live/stop", headers=h["operator"]).json()["running"] is False
    assert c.post("/api/live/maintenance", headers=h["operator"]).status_code == 409


def test_live_requires_existing_records(sample_ctx):
    sim = live.LiveSimulator(sample_ctx.factory)
    with pytest.raises(ValueError):
        sim.start(999999)
