"""Phase 3 완료 기준: 샘플 시나리오에서 심어 둔 원자재 LOT 또는 설비가 원인 후보 Top-3."""

from __future__ import annotations

from sqlalchemy import select

from app.models import AuditLog, CandidateSnapshot, DefectType, Lot


def _top(client, h, lot_no, k=3):
    r = client.get(f"/api/causes/{lot_no}", headers=h)
    assert r.status_code == 200, r.text
    return r.json(), [c["label"] for c in r.json()["candidates"][:k]]


def test_planted_raw_lot_in_top3_for_all_its_defects(sample_ctx):
    s = sample_ctx.scenario
    assert s.planted_defect_lot_nos
    for no in s.planted_defect_lot_nos:
        _, top = _top(sample_ctx.client, sample_ctx.h["viewer"], no)
        assert s.planted_raw_lot_no in top, (no, top)


def test_planted_equipment_in_top3_for_all_its_defects(sample_ctx):
    s = sample_ctx.scenario
    assert s.anomaly_defect_lot_nos
    for no in s.anomaly_defect_lot_nos:
        _, top = _top(sample_ctx.client, sample_ctx.h["viewer"], no)
        assert s.planted_equipment_name in top, (no, top)


def test_score_is_explainable(sample_ctx):
    s = sample_ctx.scenario
    data, _ = _top(sample_ctx.client, sample_ctx.h["viewer"], s.planted_defect_lot_nos[0])
    first = data["candidates"][0]
    assert set(first["factors"]) == {"spc", "lift", "arima", "condition", "design", "history"}
    assert abs(sum(first["contributions"].values()) - first["score"]) < 1e-4
    lift = first["factors"]["lift"]
    assert lift["raw"]["p_value"] < 0.01 and "불량" in lift["note"]
    # 그래프: 노드 종류 4가지, 루트 표시, 순위 부여
    types = {n["type"] for n in data["graph"]["nodes"]}
    assert types == {"LOT", "STEP", "EQUIPMENT", "CONDITION"}
    root = next(n for n in data["graph"]["nodes"] if n["id"] == data["graph"]["root"])
    assert root["is_root"] and root["status"] == "DEFECT"
    assert any(n.get("rank") == 1 for n in data["graph"]["nodes"])


def test_condition_without_deviation_gets_no_lift(sample_ctx):
    s = sample_ctx.scenario
    data, _ = _top(sample_ctx.client, sample_ctx.h["viewer"], s.planted_defect_lot_nos[0], 50)
    cond = [c for c in data["candidates"] if c["type"] == "CONDITION"]
    assert cond and all(c["factors"]["lift"]["score"] == 0 for c in cond)


def test_design_mismatch_detected(sample_ctx):
    from app.sample.generator import MISMATCH_BATCH_INDEX  # noqa: F401
    c, h = sample_ctx.client, sample_ctx.h["viewer"]
    with sample_ctx.db() as db:
        # 설계 불일치 배치의 첫 완제품
        lots = db.scalars(select(Lot.lot_no).where(Lot.lot_no.like("P-%")).order_by(Lot.created_at)).all()
    product = lots[MISMATCH_BATCH_INDEX * 4]
    data = c.get(f"/api/causes/{product}", headers=h).json()
    flagged = [x for x in data["candidates"] if x["factors"]["design"]["score"] > 0]
    assert flagged and any("비율" in x["factors"]["design"]["note"] for x in flagged)


def test_downstream_trace_lot_and_equipment(sample_ctx):
    c, h, s = sample_ctx.client, sample_ctx.h["viewer"], sample_ctx.scenario
    with sample_ctx.db() as db:
        lot_id = db.scalar(select(Lot.id).where(Lot.lot_no == s.planted_raw_lot_no))
    r = c.get(f"/api/trace/downstream/lot:{lot_id}", headers=h).json()
    nos = {l["lot_no"] for l in r["lots"]}
    assert set(s.planted_defect_lot_nos) <= nos and r["finished_count"] >= 16

    eq = f"eq:{s.planted_equipment_id}"
    assert c.get(f"/api/trace/downstream/{eq}", headers=h).status_code == 422  # 기간 필요
    r = c.get(f"/api/trace/downstream/{eq}", params={"at": s.anomaly_from.isoformat()}, headers=h).json()
    assert r["window"] and r["finished_count"] > 0
    assert c.get("/api/trace/downstream/xx:1", headers=h).status_code == 422


def test_judgment_flow_snapshot_root_cause_history(fresh_sample_ctx):
    ctx = fresh_sample_ctx
    c, op = ctx.client, ctx.h["operator"]
    s = ctx.scenario
    with ctx.db() as db:
        dtype = db.scalar(select(DefectType).where(DefectType.code == "STR-LOW"))
        # 아직 판정 안 된, 원자재 LOT 영향 배치의 완제품
        target = db.scalar(
            select(Lot).where(Lot.lot_no.like(f"P-{s.affected_batch_lot_nos[0][3:]}%"), Lot.status == "NORMAL")
        )
    assert target is not None
    assert c.post("/api/defects", json={"lot_no": target.lot_no, "defect_type_id": dtype.id},
                  headers=ctx.h["viewer"]).status_code == 403
    r = c.post("/api/defects", json={"lot_no": f" {target.lot_no} ", "defect_type_id": dtype.id, "memo": "강도 미달"},
               headers=op)
    assert r.status_code == 201, r.text
    body = r.json()
    jid = body["judgment"]["id"]
    assert body["analysis"]["candidates"]
    assert c.get(f"/api/lots/{target.lot_no}", headers=op).json()["status"] == "DEFECT"
    with ctx.db() as db:
        assert db.scalar(select(CandidateSnapshot).where(CandidateSnapshot.judgment_id == jid)) is not None

    planted = next(x for x in body["analysis"]["candidates"] if x["label"] == s.planted_raw_lot_no)
    before_hist = planted["factors"]["history"]["score"]
    r = c.post(f"/api/defects/{jid}/root-cause", json={"node": planted["node"], "confidence": 1.0}, headers=op)
    assert r.status_code == 201 and r.json()["root_causes"][0]["label"] == s.planted_raw_lot_no

    # (f) 갱신: 다음 분석부터 같은 노드 확정 이력 반영
    after = c.get(f"/api/causes/{s.planted_defect_lot_nos[0]}", headers=op).json()
    hist = next(x for x in after["candidates"] if x["label"] == s.planted_raw_lot_no)["factors"]["history"]
    assert hist["score"] > before_hist and hist["raw"]["same_node"] == 1

    bad = c.post(f"/api/defects/{jid}/root-cause", json={"node": "lot:999999"}, headers=op)
    assert bad.status_code == 404

    # 판정 취소: 관리자만, 상태 원복
    assert c.post(f"/api/defects/{jid}/cancel", headers=op).status_code == 403
    assert c.post(f"/api/defects/{jid}/cancel", headers=ctx.h["admin"]).status_code == 200
    assert c.get(f"/api/lots/{target.lot_no}", headers=op).json()["status"] == "NORMAL"
    with ctx.db() as db:
        actions = set(db.scalars(select(AuditLog.action)))
    assert {"judge", "confirm_root_cause", "cancel_judgment"} <= actions


def test_attachment_roundtrip(fresh_sample_ctx, tmp_path, monkeypatch):
    from app.api import defects as defects_api

    monkeypatch.setattr(defects_api, "ATTACH_DIR", tmp_path)
    ctx = fresh_sample_ctx
    jid = ctx.client.get("/api/defects?limit=1", headers=ctx.h["viewer"]).json()[0]["id"]
    r = ctx.client.post(f"/api/defects/{jid}/attachments", headers=ctx.h["operator"],
                        files={"file": ("사진 1.jpg", b"abc", "image/jpeg")})
    assert r.status_code == 200
    name = r.json()["attachments"][-1]
    got = ctx.client.get(f"/api/defects/{jid}/attachments/{name}", headers=ctx.h["viewer"])
    assert got.status_code == 200 and got.content == b"abc"


def test_bulk_status_permissions_and_audit(fresh_sample_ctx):
    ctx = fresh_sample_ctx
    c = ctx.client
    lots = [l["lot_no"] for l in c.get("/api/lots?q=P-2609&status=NORMAL&limit=3", headers=ctx.h["viewer"]).json()]
    body = {"lot_nos": lots, "status": "QUARANTINE", "reason": "원자재 LOT 영향 범위"}
    assert c.post("/api/lots/bulk-status", json=body, headers=ctx.h["viewer"]).status_code == 403
    r = c.post("/api/lots/bulk-status", json=body, headers=ctx.h["operator"])
    assert r.json()["changed"] == 3
    release = {"lot_nos": lots, "status": "NORMAL", "reason": "조사 완료"}
    assert c.post("/api/lots/bulk-status", json=release, headers=ctx.h["operator"]).status_code == 403
    assert c.post("/api/lots/bulk-status", json=release, headers=ctx.h["admin"]).json()["changed"] == 3
    missing = c.post("/api/lots/bulk-status", json={**body, "lot_nos": ["없는LOT"]}, headers=ctx.h["operator"])
    assert missing.status_code == 404
    with ctx.db() as db:
        assert db.scalar(select(AuditLog).where(AuditLog.action == "bulk_status")) is not None
