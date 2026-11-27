"""사출기 3호 공정 기록(문제 02): 시스템이 스스로 내린 판단이 데이터와 맞는지 고정한다.

데이터에서 사람이 확인한 사실
  - 불량률은 금형 온도를 따라간다 (51~56℃ ≈1.5%, 58℃ 이상 ≈9%)
  - '신입'·'사출 압력 80'·'냉각 16초'는 겉보기 원인: 같은 금형 온도에서는 차이 없음 (03-06 08:00 기술자·75MPa·65℃ → 10.3%)
  - 금형 온도 상승은 신입 투입(03-05) 전인 03-04 13:00 부터
  - 03-06 09:00 정지 뒤 금형 온도 52℃, 불량률 정상화 (정지 중 조치 기록 없음)
  - 수지 온도가 낮은 시간(224~226℃)엔 미성형이 최다 → 보조 요인
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.auth.provider import create_user
from app.models import ConditionDefinition, DefectJudgment, EquipmentEvent, Lot, Role
from app.sample.process_log import import_process_log, load_mapping
from tests.conftest import Ctx, _client_for, _login, _make_session_factory

SAMPLE = Path(__file__).resolve().parents[3] / "samples" / "injection_m3"


@pytest.fixture(scope="module")
def inj():
    factory = _make_session_factory()
    with factory() as db:
        create_user(db, "admin", "admin1234", Role.ADMIN)
        db.commit()
        summary = import_process_log(db, SAMPLE / "hourly_2026-03-04_06.csv", load_mapping(SAMPLE / "mapping.json"))
    client = _client_for(factory)
    h = {"viewer": _login(client, "viewer", "viewer1234"), "operator": _login(client, "operator", "operator1234"),
         "admin": _login(client, "admin", "admin1234")}
    return Ctx(factory, client, h, summary)


def _eq_id(ctx):
    return ctx.scenario.equipment_id


def test_import_matches_sheet_totals(inj):
    s = inj.scenario
    assert (s.lots, s.events, int(s.produced), int(s.defects)) == (23, 1, 6785, 277)
    with inj.db() as db:
        day = {}
        for lot, dq in db.execute(select(Lot, DefectJudgment.defect_qty).outerjoin(DefectJudgment, DefectJudgment.lot_id == Lot.id)):
            d = day.setdefault(lot.created_at.date().isoformat(), [0, 0])
            d[0] += lot.qty
            d[1] += dq or 0
        assert day == {"2026-03-04": [2354, 40], "2026-03-05": [2331, 187], "2026-03-06": [2100, 50]}
        ev = db.scalar(select(EquipmentEvent))
        assert ev.kind == "STOP" and ev.started_at == datetime(2026, 3, 6, 9, 0)
        assert db.scalar(select(func.count()).select_from(DefectJudgment)) == 23


def test_system_decides_mold_temperature_and_rejects_confounders(inj):
    r = inj.client.get(f"/api/analysis/drivers?equipment_id={_eq_id(inj)}", headers=inj.h["viewer"]).json()
    v = r["verdict"]
    assert r["status"] == "OK" and v["primary"] == "금형 온도" and v["confidence"] == "높음"
    status = {f["name"]: f["status"] for f in r["factors"]}
    assert status["금형 온도"] == "PRIMARY"
    assert status["작업자"] == "EXPLAINED"
    assert status["사출 압력 설정"] == "EXPLAINED"
    assert status["냉각 시간 설정"] == "EXPLAINED"
    assert status["수지 온도"] == "SECONDARY"
    operator = next(f for f in r["factors"] if f["name"] == "작업자")
    lv = operator["similar_condition"]["levels"]
    assert abs(lv["기술자"]["rate"] - lv["신입"]["rate"]) < 0.01  # 같은 금형 온도에서 10.3% vs 10.2%
    th = r["threshold"]
    assert th["learned_max"] == 57 and th["rate_bad"] > 0.08 and th["rate_good"] < 0.02


def test_timeline_reasoning(inj):
    r = inj.client.get(f"/api/analysis/drivers?equipment_id={_eq_id(inj)}", headers=inj.h["viewer"]).json()
    text = " ".join(r["verdict"]["reasons"])
    assert "03-04 13:00" in text and "신입' 첫 등장(03-05 08:00) 이전" in text
    ev = r["timeline"]["events"][0]
    assert ev["rate_before"] > 0.08 and ev["rate_after"] < 0.02 and ev["primary_after"] < 53
    assert any("무엇을 했는지 확인" in x for x in r["verdict"]["recommendations"])
    types = {(t["type"], t["factor"]) for t in r["type_associations"]}
    assert ("미성형", "수지 온도") in types
    assert any("고객 반품" in c for c in r["caveats"])  # 데이터 주의사항 전달


def test_learned_ranges_feed_cause_tracing(inj):
    c, h = inj.client, inj.h
    assert c.post(f"/api/analysis/drivers/apply?equipment_id={_eq_id(inj)}", headers=h["viewer"]).status_code == 403
    out = c.post(f"/api/analysis/drivers/apply?equipment_id={_eq_id(inj)}", headers=h["operator"]).json()
    learned = {x["name"]: x for x in out["learned"]}
    assert learned["금형 온도"]["learned_max"] == 57 and learned["수지 온도"]["learned_min"] == 229
    # 금형 온도가 높았던 시간: 원인 추적 1순위 = 금형 온도 (수량 가중 Lift)
    top = c.get("/api/causes/IM3-20260305-1200", headers=h["viewer"]).json()["candidates"][0]
    assert top["label"] == "금형 온도" and top["factors"]["lift"]["raw"]["unit"] == "개"
    # 수지 온도가 낮았던 미성형 시간: 1순위 = 수지 온도
    top2 = c.get("/api/causes/IM3-20260304-1000", headers=h["viewer"]).json()["candidates"][0]
    assert top2["label"] == "수지 온도"
    runs = c.get("/api/lots/IM3-20260305-1200/runs", headers=h["viewer"]).json()
    mold = next(x for x in runs[0]["conditions"] if x["name"] == "금형 온도")
    assert mold["out_of_range"] and mold["learned_range"]


def test_site_defined_range_is_not_overwritten(inj):
    with inj.db() as db:
        d = db.scalar(select(ConditionDefinition).where(ConditionDefinition.name == "사출 압력 설정"))
        d.max_value = 78
        db.commit()
    inj.client.post(f"/api/analysis/drivers/apply?equipment_id={_eq_id(inj)}", headers=inj.h["operator"])
    with inj.db() as db:
        d = db.scalar(select(ConditionDefinition).where(ConditionDefinition.name == "사출 압력 설정"))
        assert d.learned_max is None and d.effective_range() == (None, 78, False)


def test_terminology_preset_and_tool(inj):
    term = inj.client.get("/api/settings/terminology", headers=inj.h["viewer"]).json()
    assert term["preset"] == "injection"
    bad = inj.client.put("/api/settings/terminology", headers=inj.h["admin"], json={"preset": "x", "overrides": {}})
    assert bad.status_code == 422
    from app.llm.tools import call_tool

    with inj.db() as db:
        res, refs = call_tool(db, "get_process_drivers", {"equipment": "사출기 3호"})
    assert res["verdict"]["primary"] == "금형 온도" and refs[0]["link"].startswith("/drivers")


def test_events_api_includes_stop(inj):
    r = inj.client.get("/api/events", headers=inj.h["viewer"],
                       params={"from": "2026-03-06T00:00", "to": "2026-03-07T00:00", "types": "equipment_event"}).json()
    assert r["events"][0]["kind"] == "STOP"


def test_same_engine_on_lot_level_data(sample_ctx):
    """구조가 다른 데이터(합성 시나리오: LOT 단위, 작업자 없음)에서도 같은 엔진이 판단한다."""
    with sample_ctx.db() as db:
        from app.models import Equipment

        mixer2 = db.scalar(select(Equipment.id).where(Equipment.name == sample_ctx.scenario.planted_equipment_name))
    r = sample_ctx.client.get(f"/api/analysis/drivers?equipment_id={mixer2}", headers=sample_ctx.h["viewer"]).json()
    assert r["status"] == "OK"
    assert r["verdict"]["primary"] == "혼합 온도"  # 심어 둔 설비 이상 = 온도 이탈
