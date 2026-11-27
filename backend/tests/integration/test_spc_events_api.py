"""SPC 위반 사건 묶기: API, 사건 단위 확인, 원인 점수 (a), 어시스턴트 도구."""

from __future__ import annotations

from sqlalchemy import func, select

from app.models import AuditLog, Characteristic, SpcViolation


def _char_id(ctx, name):
    with ctx.db() as db:
        return db.scalar(select(Characteristic.id).where(Characteristic.name == name))


def test_events_group_violations_and_cover_every_violation(sample_ctx):
    c, h = sample_ctx.client, sample_ctx.h["viewer"]
    events = c.get("/api/spc/events", params={"limit": 2000}, headers=h).json()
    violations = c.get("/api/spc/violations", params={"limit": 2000}, headers=h).json()
    assert events and len(events) < len(violations), "룰 여러 개가 함께 걸린 사건이 하나로 묶여야 함"
    ids = sorted(i for e in events for i in e["violation_ids"])
    assert ids == sorted(v["id"] for v in violations), "모든 위반이 정확히 한 사건에 들어가야 함"
    e = max(events, key=lambda x: len(x["rule_nos"]))
    assert len(e["rule_nos"]) > 1
    assert {"id", "characteristic_id", "characteristic", "owner", "direction", "rule_nos", "rules",
            "occurred_at", "lot_nos", "acknowledged", "partially_acknowledged"} <= set(e)
    assert e["id"] == min(e["violation_ids"])
    assert all(r["description"] for r in e["rules"])


def test_events_filter_by_characteristic(sample_ctx):
    c, h = sample_ctx.client, sample_ctx.h["viewer"]
    cid = _char_id(sample_ctx, "강도")
    events = c.get("/api/spc/events", params={"characteristic_id": cid}, headers=h).json()
    assert events and {e["characteristic_id"] for e in events} == {cid}


def test_characteristic_summary_counts_open_events(sample_ctx):
    rows = sample_ctx.client.get("/api/spc/characteristics", headers=sample_ctx.h["viewer"]).json()
    strength = next(r for r in rows if r["name"] == "강도")
    assert 0 < strength["open_events"] < strength["open_violations"]


def test_chart_payload_includes_events(sample_ctx):
    data = sample_ctx.client.get(f"/api/spc/{_char_id(sample_ctx, '강도')}/chart",
                                 headers=sample_ctx.h["viewer"]).json()
    assert data["events"] and len(data["events"]) < len(data["violations"])
    members = sorted(i for e in data["events"] for i in e["violations"])
    assert members == list(range(len(data["violations"]))), "관리도 위반 목록의 각 위반이 한 사건에 속해야 함"


def test_acknowledging_an_event_acknowledges_all_its_violations(fresh_sample_ctx):
    ctx = fresh_sample_ctx
    c = ctx.client
    cid = _char_id(ctx, "강도")
    events = c.get("/api/spc/events", params={"characteristic_id": cid, "acknowledged": "false"},
                   headers=ctx.h["viewer"]).json()
    target = max(events, key=lambda e: len(e["violation_ids"]))
    assert len(target["violation_ids"]) > 1

    body = {"violation_ids": target["violation_ids"]}
    assert c.post("/api/spc/events/ack", json=body, headers=ctx.h["viewer"]).status_code == 403
    r = c.post("/api/spc/events/ack", json=body, headers=ctx.h["operator"])
    assert r.status_code == 200, r.text
    assert r.json()["acknowledged"] == len(target["violation_ids"])

    with ctx.db() as db:
        rows = db.scalars(select(SpcViolation).where(SpcViolation.id.in_(target["violation_ids"]))).all()
        assert all(v.acknowledged_at is not None for v in rows)
        audits = db.scalar(select(func.count()).select_from(AuditLog).where(
            AuditLog.action == "acknowledge", AuditLog.entity == "spc_violation"))
        assert audits == len(target["violation_ids"])

    still_open = c.get("/api/spc/events", params={"characteristic_id": cid, "acknowledged": "false"},
                       headers=ctx.h["viewer"]).json()
    assert target["id"] not in {e["id"] for e in still_open}
    # 이미 다 확인된 사건을 다시 확인하면 409
    assert c.post("/api/spc/events/ack", json=body, headers=ctx.h["operator"]).status_code == 409


def test_ack_rejects_empty_or_unknown_ids(ctx):
    assert ctx.client.post("/api/spc/events/ack", json={"violation_ids": []},
                           headers=ctx.h["operator"]).status_code == 422
    assert ctx.client.post("/api/spc/events/ack", json={"violation_ids": [999999]},
                           headers=ctx.h["operator"]).status_code == 404


def test_cause_spc_factor_counts_events_not_rule_kinds(sample_ctx):
    s = sample_ctx.scenario
    lot = s.planted_defect_lot_nos[0]
    data = sample_ctx.client.get(f"/api/causes/{lot}", headers=sample_ctx.h["viewer"]).json()
    scored = [c for c in data["candidates"] if c["factors"]["spc"]["raw"].get("events")]
    assert scored, "경로상 위반 사건이 있는 후보가 있어야 함"
    for cand in scored:
        raw = cand["factors"]["spc"]["raw"]
        assert raw["events"] <= raw["violations"]
        assert "사건" in cand["factors"]["spc"]["note"]


def test_assistant_tool_returns_events(sample_ctx):
    from app.llm.tools import call_tool

    with sample_ctx.db() as db:
        out, refs = call_tool(db, "get_spc_violations", {"characteristic": "강도"})
    assert out["events"] and out["total_events"] <= out["total"]
    first = out["events"][0]
    assert {"event", "violation_ids", "characteristic", "rules", "direction", "occurred_at"} <= set(first)
    assert refs and refs[0]["kind"] == "violation"


def test_settings_saved_with_the_retired_rule_saturation_key_can_be_saved_again(ctx):
    """예전 버전에서 저장한 cause_scoring(spc_rule_saturation 포함)을 그대로 다시 저장해도 거부되지 않아야 함."""
    from app.models import Setting

    with ctx.db() as db:
        db.add(Setting(key="cause_scoring", value={"spc_rule_saturation": 3, "lift_saturation": 5.0},
                       updated_at=__import__("datetime").datetime.now()))
        db.commit()
    current = ctx.client.get("/api/settings/cause_scoring", headers=ctx.h["admin"]).json()
    assert "spc_rule_saturation" not in current and current["lift_saturation"] == 5.0
    assert current["spc_event_saturation"] == 2
    r = ctx.client.put("/api/settings/cause_scoring", json=current, headers=ctx.h["admin"])
    assert r.status_code == 200, r.text
