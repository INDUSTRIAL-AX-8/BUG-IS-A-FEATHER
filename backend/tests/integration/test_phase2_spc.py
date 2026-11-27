"""Phase 2: 심어 둔 이상이 관리도에서 위반으로 표시되는가, 기준 구간 고정, 룰 On/Off."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from app.models import AuditLog, Characteristic


def _char_id(ctx, name):
    with ctx.db() as db:
        return db.scalar(select(Characteristic.id).where(Characteristic.name == name))


def test_planted_raw_lot_flagged_on_purity_chart(sample_ctx):
    c, h, s = sample_ctx.client, sample_ctx.h["viewer"], sample_ctx.scenario
    data = c.get(f"/api/spc/{_char_id(sample_ctx, '순도')}/chart", headers=h).json()
    assert data["chart_type"] == "IMR" and data["provisional"]
    flagged = [p for p in data["points"] if 1 in p["rules"]]
    assert any(s.planted_raw_lot_no in p["lot_nos"] for p in flagged)


def test_planted_effects_on_viscosity_chart(sample_ctx):
    c, h, s = sample_ctx.client, sample_ctx.h["viewer"], sample_ctx.scenario
    data = c.get(f"/api/spc/{_char_id(sample_ctx, '점도')}/chart", headers=h).json()
    flagged_lots = {no for p in data["points"] if p["rules"] for no in p["lot_nos"]}
    assert flagged_lots & set(s.affected_batch_lot_nos), "원자재 LOT 영향 배치가 위반으로 표시돼야 함"
    anomaly_points = [
        p for p in data["points"]
        if p["rules"] and s.anomaly_from <= datetime.fromisoformat(p["time"]) <= s.anomaly_to
    ]
    assert anomaly_points, "설비 이상 기간 배치가 위반으로 표시돼야 함"


def test_xbar_r_auto_for_subgroup_5(sample_ctx):
    data = sample_ctx.client.get(
        f"/api/spc/{_char_id(sample_ctx, '중량')}/chart", headers=sample_ctx.h["viewer"]
    ).json()
    assert data["chart_type"] == "XBAR_R"
    assert all(len(p["values"]) == 5 for p in data["points"])
    assert data["limits"]["dispersion_ucl"] > 0


def test_violations_saved_and_listed(sample_ctx):
    rows = sample_ctx.client.get("/api/spc/violations?acknowledged=false", headers=sample_ctx.h["viewer"]).json()
    assert rows and {"rule_no", "description", "lot_nos"} <= set(rows[0])
    summary = sample_ctx.client.get("/api/spc/characteristics", headers=sample_ctx.h["viewer"]).json()
    assert any(r["open_violations"] > 0 for r in summary)


def test_lock_baseline_recompute_and_audit(fresh_sample_ctx):
    ctx = fresh_sample_ctx
    c = ctx.client
    cid = _char_id(ctx, "점도")
    body = {"baseline_from": "2026-09-01T00:00", "baseline_to": "2026-09-15T23:59"}
    assert c.post(f"/api/spc/{cid}/limits", json=body, headers=ctx.h["operator"]).status_code == 403
    r = c.post(f"/api/spc/{cid}/limits", json=body, headers=ctx.h["admin"])
    assert r.status_code == 200, r.text
    data = c.get(f"/api/spc/{cid}/chart", headers=ctx.h["viewer"]).json()
    assert not data["provisional"] and data["limits"]["version"] == 1
    assert all(v["id"] is not None for v in data["violations"])

    r2 = c.post(f"/api/spc/{cid}/limits", json=body, headers=ctx.h["admin"])
    assert r2.json()["version"] == 2
    with ctx.db() as db:
        logs = db.scalars(select(AuditLog).where(AuditLog.action == "recalc_limits")).all()
    assert len(logs) == 2 and logs[1].before is not None

    short = {"baseline_from": "2026-09-01T00:00", "baseline_to": "2026-09-02T00:00"}
    bad = c.post(f"/api/spc/{cid}/limits", json=short, headers=ctx.h["admin"])
    assert bad.status_code == 422 and "최소" in bad.json()["message"]


def test_rule_toggle_per_characteristic(fresh_sample_ctx):
    ctx = fresh_sample_ctx
    c, admin = ctx.client, ctx.h["admin"]
    cid = _char_id(ctx, "순도")
    char = c.get(f"/api/characteristics/{cid}", headers=admin).json()
    char["rule_config"] = {"1": {"enabled": False}}
    assert c.put(f"/api/characteristics/{cid}", json=char, headers=admin).status_code == 200
    data = c.get(f"/api/spc/{cid}/chart", headers=admin).json()
    assert all(1 not in p["rules"] for p in data["points"])
    assert next(r for r in data["rules"] if r["rule_no"] == 1)["enabled"] is False

    char["rule_config"] = {"2": {"params": {"count": 1}}}
    bad = c.put(f"/api/characteristics/{cid}", json=char, headers=admin)
    assert bad.status_code == 422 and "넬슨룰" in bad.json()["message"]


def test_ack_violation(fresh_sample_ctx):
    ctx = fresh_sample_ctx
    rows = ctx.client.get("/api/spc/violations?acknowledged=false&limit=1", headers=ctx.h["viewer"]).json()
    vid = rows[0]["id"]
    assert ctx.client.post(f"/api/spc/violations/{vid}/ack", headers=ctx.h["viewer"]).status_code == 403
    assert ctx.client.post(f"/api/spc/violations/{vid}/ack", headers=ctx.h["operator"]).status_code == 200
    # 재계산 후에도 확인 상태 유지
    ctx.client.post("/api/spc/recompute", headers=ctx.h["operator"])
    acked = ctx.client.get("/api/spc/violations?acknowledged=true", headers=ctx.h["viewer"]).json()
    assert any(r["id"] == vid for r in acked)
