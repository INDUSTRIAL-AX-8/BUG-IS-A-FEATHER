"""불량 의심 → 실무자 판단 → 불량 적재, 전체 공정 흐름, 생산 대시보드."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.auth.provider import create_user
from app.live import simulator as live
from app.models import DefectJudgment, DefectSuspect, DefectType, ProcessRun, Role
from app.sample.process_log import import_process_log, load_mapping
from app.services.driver_service import analyze_equipment
from tests.conftest import Ctx, _client_for, _login, _make_session_factory

SAMPLE = Path(__file__).resolve().parents[3] / "samples" / "injection_m3"


@pytest.fixture(scope="module")
def rv():
    factory = _make_session_factory()
    with factory() as db:
        create_user(db, "admin", "admin1234", Role.ADMIN)
        db.commit()
        s = import_process_log(db, SAMPLE / "hourly_2026-03-04_06.csv", load_mapping(SAMPLE / "mapping.json"))
        analyze_equipment(db, s.equipment_id, apply_learned=True)
    sim = live.LiveSimulator(factory)
    live.set_simulator(sim)
    sim.start(None, "drift", interval_sec=600, seed=7)
    sim.stop()
    sim.state.running = True
    records = [sim.tick() for _ in range(12)]
    client = _client_for(factory)
    h = {"viewer": _login(client, "viewer", "viewer1234"), "operator": _login(client, "operator", "operator1234"),
         "admin": _login(client, "admin", "admin1234")}
    yield Ctx(factory, client, h, {"summary": s, "records": records}), sim
    live.set_simulator(None)


def test_full_line_route_and_runs(rv):
    ctx, sim = rv
    route = [r["step"] for r in sim.status()["route"]]
    assert route == ["원료 건조", "사출", "취출·냉각", "외관 검사", "포장·출하"]
    rec = next(r for r in ctx.scenario["records"] if r["type"] == "record")
    assert [x["step"] for x in rec["route"]] == route
    assert {i["table"] for i in rec["inserts"]} >= {"lot", "process_run", "condition_value"}
    with ctx.db() as db:
        steps_per_lot = db.scalar(select(func.count()).select_from(ProcessRun).where(ProcessRun.lot_id == rec["lot_id"]))
    assert steps_per_lot == 5


def test_live_creates_suspects_not_defects(rv):
    ctx, _ = rv
    with ctx.db() as db:
        assert db.scalar(select(func.count()).select_from(DefectJudgment)) == 23  # 원본 이력만, 자동 적재 없음
        sources = set(db.scalars(select(DefectSuspect.source)))
    assert "INSPECTION" in sources and "CONDITION" in sources  # 검사 불합격 + 금형 온도 이탈
    summ = ctx.client.get("/api/suspects/summary", headers=ctx.h["viewer"]).json()
    stages = [s["stage"] for s in summ["stages"]]
    assert stages == sorted(stages, key=lambda x: ["원료 건조", "사출", "취출·냉각", "외관 검사", "포장·출하"].index(x))
    assert summ["pending_total"] > 0


def test_confirm_reject_flow_and_dashboard(rv):
    ctx, _ = rv
    c, h = ctx.client, ctx.h
    before = c.get("/api/production/dashboard", headers=h["viewer"]).json()["kpi"]
    insp = c.get("/api/suspects?source=INSPECTION", headers=h["viewer"]).json()
    cond = c.get("/api/suspects?source=CONDITION", headers=h["viewer"]).json()
    s1, s2 = insp[0], insp[1]
    assert c.post(f"/api/suspects/{s1['id']}/confirm", headers=h["viewer"], json={}).status_code == 403
    # 검사 불합격: 의심 수량 그대로 확정 (유형 추정이 없으면 지정)
    with ctx.db() as db:
        tid = db.scalar(select(DefectType.id).limit(1))
    r = c.post(f"/api/suspects/{s1['id']}/confirm", headers=h["operator"], json={"defect_type_id": s1["defect_type_id"] or tid})
    assert r.status_code == 200 and r.json()["judgment_id"]
    assert c.post(f"/api/suspects/{s1['id']}/confirm", headers=h["operator"], json={}).status_code == 409
    # 조건 이탈: 재검 수량 없이 확정 불가 → 수량 입력 후 확정
    bad = c.post(f"/api/suspects/{cond[0]['id']}/confirm", headers=h["operator"], json={"defect_type_id": tid})
    assert bad.status_code == 422 and "수량" in bad.json()["message"]
    assert c.post(f"/api/suspects/{cond[0]['id']}/confirm", headers=h["operator"], json={"qty": 12, "defect_type_id": tid}).status_code == 200
    # 불량 아님
    assert c.post(f"/api/suspects/{s2['id']}/reject", headers=h["operator"], json={"reason": "검사 과검출"}).json()["status"] == "REJECTED"
    after = c.get("/api/production/dashboard", headers=h["viewer"]).json()
    assert after["kpi"]["confirmed_defects"] == before["confirmed_defects"] + s1["suspected_qty"] + 12
    stages = {x["stage"] for x in after["by_stage"]}
    assert "외관 검사" in stages and "사출" in stages and "이력·직접 등록" in stages
    with ctx.db() as db:
        j = db.get(DefectJudgment, r.json()["judgment_id"])
        assert j.defect_qty == s1["suspected_qty"] and "외관 검사" in j.memo


def test_bulk_review(rv):
    ctx, _ = rv
    c, h = ctx.client, ctx.h
    pending = c.get("/api/suspects?status=PENDING", headers=h["viewer"]).json()
    ids = [p["id"] for p in pending[:3]]
    r = c.post("/api/suspects/bulk", headers=h["operator"], json={"ids": ids, "action": "reject", "reason": "재검 결과 양품"}).json()
    assert r["done"] == 3
    assert all(x["status"] == "REJECTED" for x in c.get("/api/suspects?status=REJECTED", headers=h["viewer"]).json() if x["id"] in ids)


def test_production_dashboard_capa_and_top_causes(rv):
    ctx, _ = rv
    d = ctx.client.get("/api/production/dashboard", headers=ctx.h["viewer"]).json()
    eq = d["equipment"][0]
    assert eq["name"] == "사출기 3호" and eq["capacity_per_hour"] == 300 and eq["capacity_source"] == "설정"
    k = d["kpi"]
    assert 0.8 < k["utilization"] < 1.0 and k["stops"] >= 1
    assert d["top_causes"][0]["cause"].startswith("금형 온도")
    assert d["by_type"] and d["series"]


def test_defect_data_csv(rv):
    ctx, _ = rv
    r = ctx.client.get("/api/defects/export.csv", headers=ctx.h["viewer"])
    text = r.content.decode("utf-8-sig")
    assert r.status_code == 200 and text.splitlines()[0].startswith("판정ID")
    assert "이력·직접 등록" in text and "외관 검사" in text  # 이력 + 의심 확정분


def test_defect_list_returns_quantity(rv):
    """불량 데이터 화면이 쓰는 목록 API 가 판정별 불량 수량을 돌려준다 (없으면 화면이 판정 1건 = 1개로 셈)."""
    ctx, _ = rv
    rows = ctx.client.get("/api/defects", params={"limit": 2000}, headers=ctx.h["viewer"]).json()
    with ctx.db() as db:
        stored = dict(db.execute(select(DefectJudgment.id, DefectJudgment.defect_qty)
                                 .where(DefectJudgment.is_cancelled.is_(False))).all())
    assert rows and {r["id"]: r["defect_qty"] for r in rows} == stored
    assert all(r["lot_qty"] for r in rows)
    # 집계 기록은 판정 1건에 여러 개 불량: 수량 합계가 판정 건수보다 커야 한다
    assert sum(r["defect_qty"] for r in rows if r["defect_qty"] is not None) > len(rows)


def test_overview_period_compare_and_summary(rv):
    ctx, _ = rv
    c, h = ctx.client, ctx.h
    full = c.get("/api/production/overview", headers=h["viewer"]).json()
    assert full["bucket"] == "day" and full["trend"] and full["by_equipment"][0]["name"] == "사출기 3호"
    assert full["targets"]["defect_rate"] == 0.015 and full["summary"]
    assert "금형 온도" in full["summary"][0]
    assert full["data_range"]["from"] and full["previous"] is None  # 전체 기간 앞에는 기록 없음
    # 짧은 기간: 일 단위, 직전 같은 길이 기간과 비교
    r = c.get("/api/production/overview", headers=h["viewer"], params={"from": "2026-03-05T12:00:00", "to": "2026-03-06T23:59:59"}).json()
    assert r["bucket"] == "day" and len(r["trend"]) <= 3
    assert r["previous"] and r["previous"]["kpi"]["produced"] > 0
    assert r["previous"]["period"]["to"] < r["period"]["from"]
    eq = r["by_equipment"][0]
    assert eq["produced"] == r["kpi"]["produced"] and eq["defects"] == r["kpi"]["confirmed_defects"]


def test_production_range(rv):
    ctx, _ = rv
    r = ctx.client.get("/api/production/range", headers=ctx.h["viewer"]).json()
    assert r["from"].startswith("2026-03-04") and r["to"] >= r["from"]
