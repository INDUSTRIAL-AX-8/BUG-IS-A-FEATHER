"""시연 기록 구분·삭제·끄기, 가져오기 때 관리한계 기준 구간 고정, 임시 한계 위반은 경보로 세지 않음."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.auth.provider import create_user
from app.config import get_settings
from app.live import simulator as live
from app.models import DefectJudgment, DefectSuspect, EquipmentEvent, Lot, Role, SpcViolation
from app.sample.process_log import import_process_log, load_mapping
from app.services.driver_service import analyze_equipment
from tests.conftest import Ctx, _client_for, _login, _make_session_factory

SAMPLE = Path(__file__).resolve().parents[3] / "samples" / "injection_m3"
REAL_LOTS = 23


@pytest.fixture()
def imported():
    """사출기 3호 기록만 가져온 DB (시연 전)."""
    factory = _make_session_factory()
    with factory() as db:
        create_user(db, "admin", "admin1234", Role.ADMIN)
        db.commit()
        s = import_process_log(db, SAMPLE / "hourly_2026-03-04_06.csv", load_mapping(SAMPLE / "mapping.json"))
        analyze_equipment(db, s.equipment_id, apply_learned=True)
    sim = live.LiveSimulator(factory)
    live.set_simulator(sim)
    client = _client_for(factory)
    h = {name: _login(client, name, f"{name}1234") for name in ("admin", "operator", "viewer")}
    yield Ctx(factory, client, h), sim
    live.set_simulator(None)


def _run_live(sim, ticks: int = 12) -> None:
    sim.start(None, "drift", interval_sec=600, seed=7)
    sim.stop()
    sim.state.running = True
    for _ in range(ticks):
        sim.tick()
    sim.state.running = False


def _chart(ctx, name: str) -> dict:
    chars = {c["name"]: c for c in ctx.client.get("/api/spc/characteristics", headers=ctx.h["viewer"]).json()}
    return {"summary": chars[name],
            **ctx.client.get(f"/api/spc/{chars[name]['id']}/chart", headers=ctx.h["viewer"]).json()}


# ------------------------------------------------------------------ 기준 구간

def test_import_locks_baseline_on_stable_window(imported):
    ctx, _ = imported
    mold = _chart(ctx, "금형 온도")
    assert mold["provisional"] is False and mold["summary"]["provisional"] is False
    assert mold["limits"]["baseline_from"].startswith("2026-03-06T10:00")
    assert "6개" in mold["message"] and "권장" in mold["message"]  # 기록이 짧아 권장 수 미달임을 알린다
    by_time = {p["time"][:13]: p for p in mold["points"]}
    # 정상 운전값(51℃)은 위반이 아니고, 오르기 시작한 점부터 한계 이탈(R1)로 잡힌다
    assert by_time["2026-03-04T08"]["value"] == 51 and by_time["2026-03-04T08"]["rules"] == []
    assert by_time["2026-03-06T12"]["rules"] == []
    assert 1 in by_time["2026-03-04T12"]["rules"] and 1 in by_time["2026-03-05T12"]["rules"]
    assert mold["limits"]["lcl"] < 51 < mold["limits"]["ucl"] < 53


def test_characteristic_without_baseline_stays_provisional_and_is_not_an_alarm(imported):
    ctx, _ = imported
    resin = _chart(ctx, "수지 온도")
    assert resin["provisional"] is True and resin["summary"]["provisional"] is True
    rows = ctx.client.get("/api/spc/violations", headers=ctx.h["viewer"], params={"acknowledged": False}).json()
    by_char = {name: {r["provisional"] for r in rows if r["characteristic"] == name} for name in ("금형 온도", "수지 온도")}
    assert by_char == {"금형 온도": {False}, "수지 온도": {True}}


def test_live_does_not_raise_suspects_from_provisional_limits(imported):
    ctx, sim = imported
    _run_live(sim)
    with ctx.db() as db:
        spc_details = list(db.scalars(select(DefectSuspect.detail).where(DefectSuspect.source == "SPC")))
    assert spc_details and all("금형 온도" in d for d in spc_details)  # 임시 한계인 수지 온도는 의심으로 올리지 않음


# ------------------------------------------------------------------ 시연 기록

def test_live_records_are_marked_simulated(imported):
    ctx, sim = imported
    _run_live(sim, ticks=12)
    with ctx.db() as db:
        assert db.scalar(select(func.count()).select_from(Lot).where(Lot.is_simulated.is_(False))) == REAL_LOTS
        simulated = db.scalar(select(func.count()).select_from(Lot).where(Lot.is_simulated.is_(True)))
        assert simulated >= 8  # 정비 틱은 LOT 을 만들지 않는다
        assert db.scalar(select(func.count()).select_from(EquipmentEvent).where(EquipmentEvent.is_simulated.is_(False))) == 1
    lots = ctx.client.get("/api/lots", headers=ctx.h["viewer"], params={"limit": 300}).json()
    assert sum(l["is_simulated"] for l in lots) == simulated
    status = ctx.client.get("/api/live/status", headers=ctx.h["viewer"]).json()
    assert status["enabled"] is True and status["simulated"]["lots"] == simulated


def test_purge_removes_only_simulated_records(imported):
    ctx, sim = imported

    def snapshot():
        with ctx.db() as db:
            return {
                "lots": db.scalar(select(func.count()).select_from(Lot)),
                "judgments": db.scalar(select(func.count()).select_from(DefectJudgment)),
                "defect_qty": db.scalar(select(func.sum(DefectJudgment.defect_qty))),
                "events": db.scalar(select(func.count()).select_from(EquipmentEvent)),
                "suspects": db.scalar(select(func.count()).select_from(DefectSuspect)),
                "violations": sorted(db.execute(select(SpcViolation.characteristic_id, SpcViolation.rule_no,
                                                        SpcViolation.occurred_at)).all()),
            }

    before = snapshot()
    _run_live(sim, ticks=14)
    # 시연 의심 하나를 불량으로 확정해 판정까지 생기게 한다
    pending = ctx.client.get("/api/suspects", headers=ctx.h["operator"], params={"status": "PENDING"}).json()
    rows = pending["items"] if isinstance(pending, dict) else pending
    insp = next(s for s in rows if s["source"] == "INSPECTION")
    r = ctx.client.post(f"/api/suspects/{insp['id']}/confirm", headers=ctx.h["operator"], json={})
    assert r.status_code == 200, r.text
    assert snapshot() != before

    assert ctx.client.delete("/api/live/records", headers=ctx.h["operator"]).status_code == 403  # 관리자만
    sim.state.running = True
    assert ctx.client.delete("/api/live/records", headers=ctx.h["admin"]).status_code == 409  # 켜져 있으면 거부
    sim.state.running = False
    done = ctx.client.delete("/api/live/records", headers=ctx.h["admin"])
    assert done.status_code == 200 and done.json()["lots"] >= 8 and done.json()["judgments"] == 1

    assert snapshot() == before  # 실제 기록·판정·위반은 그대로
    status = ctx.client.get("/api/live/status", headers=ctx.h["viewer"]).json()
    assert status["simulated"] == {"lots": 0, "equipment_events": 0}


def test_simulator_can_be_switched_off_for_production(imported, monkeypatch):
    ctx, _ = imported
    monkeypatch.setattr(get_settings(), "live_simulator_enabled", False)
    r = ctx.client.post("/api/live/start", headers=ctx.h["operator"], json={"scenario": "drift", "interval_sec": 600})
    assert r.status_code == 403 and "시연" in r.json()["message"]
    assert ctx.client.get("/api/live/status", headers=ctx.h["viewer"]).json()["enabled"] is False
    with ctx.db() as db:
        assert db.scalar(select(func.count()).select_from(Lot).where(Lot.is_simulated.is_(True))) == 0
