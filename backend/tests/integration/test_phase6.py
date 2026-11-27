"""Phase 6: 컬럼 매핑 업로드, REST 수집(같은 검증), 정확도 지표, 이벤트 API, 감사 로그, 권한."""

from __future__ import annotations

import io

import pytest
from sqlalchemy import select

from app.models import AuditLog, Lot


@pytest.fixture()
def up(fresh_sample_ctx, tmp_path, monkeypatch):
    from app.api import ingest as ingest_api

    monkeypatch.setattr(ingest_api, "UPLOAD_DIR", tmp_path)
    return fresh_sample_ctx


def _upload(ctx, name: str, content: bytes):
    return ctx.client.post("/api/upload", headers=ctx.h["operator"], files={"file": (name, content, "text/csv")})


def test_targets_list_includes_dynamic_conditions(sample_ctx):
    t = {x["target"]: x for x in sample_ctx.client.get("/api/ingest/targets", headers=sample_ctx.h["viewer"]).json()}
    assert set(t) == {"lots", "edges", "runs", "measurements", "defects"}
    assert any(d["name"] == "cond:혼합 온도" for d in t["runs"]["dynamic"])


def test_upload_mapping_preview_errors_and_commit(up):
    ctx = up
    # 현장 양식: 열 이름이 시스템과 다르고, CP949 인코딩
    csv_text = (
        "로트번호,품목,생성일시,수량\n"
        "NEW-001,원자재-A (주원료),2026-11-01 08:00,100\n"
        "NEW-002,원자재-A (주원료),2026/11/01 09:30,\n"
        "NEW-003,없는품목,2026-11-01 10:00,5\n"
        ",원자재-A (주원료),2026-11-01 11:00,1\n"
        "NEW-005,원자재-A (주원료),어제,1\n"
        "NEW-001,원자재-A (주원료),2026-11-01 12:00,1\n"
    )
    r = _upload(ctx, "lots.csv", csv_text.encode("cp949"))
    assert r.status_code == 200, r.text
    u = r.json()
    assert u["columns"] == ["로트번호", "품목", "생성일시", "수량"] and u["row_count"] == 6
    mapping = {"lot_no": "로트번호", "item_type": "품목", "created_at": "생성일시", "qty": "수량"}

    missing = ctx.client.post(f"/api/upload/{u['upload_id']}/preview", headers=ctx.h["operator"],
                              json={"target": "lots", "mapping": {"lot_no": "로트번호"}})
    assert missing.status_code == 422 and "필수 필드" in missing.json()["message"]

    p = ctx.client.post(f"/api/upload/{u['upload_id']}/preview", headers=ctx.h["operator"],
                        json={"target": "lots", "mapping": mapping}).json()
    assert p["valid"] == 2 and p["invalid"] == 4
    msgs = {e["row"]: " ".join(e["messages"]) for e in p["errors"]}
    assert "등록되지 않은" in msgs[4] and "필수값 누락" in msgs[5] and "날짜" in msgs[6] and "이미 있는 LOT" in msgs[7]

    err = ctx.client.get(p["errors_url"].removeprefix("/api") and f"/api/upload/{u['upload_id']}/errors.csv", headers=ctx.h["viewer"])
    assert err.status_code == 200
    err_text = err.content.decode("utf-8-sig")
    assert err_text.count("\n") == 5 and "없는품목" in err_text  # 헤더 + 오류 4행

    c = ctx.client.post(f"/api/upload/{u['upload_id']}/commit", headers=ctx.h["operator"],
                        json={"target": "lots", "mapping": mapping}).json()
    assert c["inserted"] == 2 and c["invalid"] == 4
    assert ctx.client.get("/api/lots/NEW-002", headers=ctx.h["viewer"]).json()["qty"] is None


def test_upload_edges_cycle_detection_within_file(up):
    ctx = up
    s = ctx.scenario
    product = s.planted_defect_lot_nos[0]
    text = (f"parent,child\n{s.planted_raw_lot_no},RA-0001\nRA-0001,RA-0002\nRA-0002,RA-0001\n"
            f"{product},{s.planted_raw_lot_no}\n")
    u = _upload(ctx, "edges.csv", text.encode()).json()
    p = ctx.client.post(f"/api/upload/{u['upload_id']}/preview", headers=ctx.h["operator"],
                        json={"target": "edges", "mapping": {"parent_lot_no": "parent", "child_lot_no": "child"}}).json()
    msgs = {e["row"]: " ".join(e["messages"]) for e in p["errors"]}
    assert p["valid"] == 2
    assert "순환" in msgs[4]  # 파일 안에서 RA-0001 → RA-0002 → RA-0001
    assert "순환" in msgs[5]  # 제품 → 원자재 (DB 계보와 순환)


def test_excel_upload_runs_with_conditions(up):
    from openpyxl import Workbook

    ctx = up
    wb = Workbook()
    ws = wb.active
    ws.append(["LOT", "공정", "설비", "시작", "온도"])
    ws.append(["RA-0001", "혼합", "혼합기-1", "2026-11-02 08:00", "65.5"])
    ws.append(["RA-0001", "혼합", "혼합기-1", "2026-11-02 09:00", "뜨거움"])
    buf = io.BytesIO()
    wb.save(buf)
    r = ctx.client.post("/api/upload", headers=ctx.h["operator"],
                        files={"file": ("runs.xlsx", buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    u = r.json()
    mapping = {"lot_no": "LOT", "process_step": "공정", "equipment": "설비", "started_at": "시작", "cond:혼합 온도": "온도"}
    c = ctx.client.post(f"/api/upload/{u['upload_id']}/commit", headers=ctx.h["operator"],
                        json={"target": "runs", "mapping": mapping}).json()
    assert c["inserted"] == 1 and c["invalid"] == 1
    runs = ctx.client.get("/api/lots/RA-0001/runs", headers=ctx.h["viewer"]).json()
    new = [r for r in runs if r["process_step_name"] == "혼합"]
    assert new and new[0]["conditions"][0]["value"] == 65.5


def test_rest_ingest_same_validation(up):
    ctx = up
    rows = [{"lot_no": "API-1", "item_type": "완제품-P", "created_at": "2026-11-03T08:00"},
            {"lot_no": "API-2", "item_type": "완제품-P", "created_at": "bad"}]
    dry = ctx.client.post("/api/ingest/lots", headers=ctx.h["operator"], json={"rows": rows, "dry_run": True}).json()
    assert dry["valid"] == 1 and dry["invalid"] == 1 and "inserted" not in dry
    assert ctx.client.get("/api/lots/API-1", headers=ctx.h["viewer"]).status_code == 404
    real = ctx.client.post("/api/ingest/lots", headers=ctx.h["operator"], json={"rows": rows}).json()
    assert real["inserted"] == 1
    m = ctx.client.post("/api/ingest/measurements", headers=ctx.h["operator"], json={"rows": [
        {"characteristic": "강도", "lot_no": "API-1", "measured_at": "2026-11-03 09:00", "value": "49.5"}]}).json()
    assert m["inserted"] == 1
    assert ctx.client.post("/api/ingest/nope", headers=ctx.h["operator"], json={"rows": [{}]}).status_code == 422
    assert ctx.client.post("/api/ingest/lots", headers=ctx.h["viewer"], json={"rows": rows}).status_code == 403
    with ctx.db() as db:
        assert db.scalar(select(AuditLog).where(AuditLog.action == "ingest"))


def test_mapping_templates(up):
    ctx = up
    body = {"name": "A공장 LOT 양식", "target": "lots", "column_map": {"lot_no": "로트번호"}}
    t = ctx.client.post("/api/mapping-templates", headers=ctx.h["operator"], json=body).json()
    body["column_map"]["item_type"] = "품목"
    t2 = ctx.client.post("/api/mapping-templates", headers=ctx.h["operator"], json=body).json()
    assert t2["id"] == t["id"] and t2["column_map"]["item_type"] == "품목"
    lst = ctx.client.get("/api/mapping-templates?target=lots", headers=ctx.h["viewer"]).json()
    assert [x["name"] for x in lst] == ["A공장 LOT 양식"]
    assert ctx.client.delete(f"/api/mapping-templates/{t['id']}", headers=ctx.h["operator"]).status_code == 204


def test_accuracy_from_feedback_history(fresh_sample_ctx):
    from app.sample.generator import simulate_feedback

    ctx = fresh_sample_ctx
    with ctx.db() as db:
        out = simulate_feedback(db, ctx.scenario, confirm_prob=1.0)
    assert out["원인 확정"] == len(ctx.scenario.planted_defect_lot_nos) + len(ctx.scenario.anomaly_defect_lot_nos)
    acc = ctx.client.get("/api/accuracy?bucket=week", headers=ctx.h["viewer"]).json()
    assert acc["overall"]["total"] == out["원인 확정"]
    assert acc["series"] and acc["series"][-1]["cumulative"]["total"] == acc["overall"]["total"]
    # 판정 시점 기준: 새 원인의 첫 불량들은 근거가 적어 순위가 낮고, 판정·확정이 쌓이면 1위로 올라온다
    from app.api.accuracy import accuracy_rows

    with ctx.db() as db:
        ranks = [r["rank"] for r in accuracy_rows(db)]
    assert acc["overall"]["top5"] >= 0.8
    later = ranks[len(ranks) // 2:]
    assert sum(r == 1 for r in later) / len(later) >= 0.9
    assert acc["overall"]["top1"] < 1.0  # 학습 효과가 보이도록 초기 오답이 존재


def test_events_api(sample_ctx):
    c, h = sample_ctx.client, sample_ctx.h["viewer"]
    r = c.get("/api/events", headers=h, params={"from": "2026-09-22T00:00", "to": "2026-09-24T12:00"}).json()
    kinds = {e["type"] for e in r["events"]}
    assert {"run_start", "run_end", "measurement", "judgment"} <= kinds
    times = [e["at"] for e in r["events"]]
    assert times == sorted(times)
    eq_events = [e for e in r["events"] if e["type"] == "run_start" and e["equipment"]]
    assert eq_events and "pos" in eq_events[0]["equipment"]
    only = c.get("/api/events", headers=h, params={"from": "2026-09-01", "to": "2026-11-01", "types": "violation"}).json()
    assert only["events"] and all(e["type"] == "violation" for e in only["events"])
    assert c.get("/api/events", headers=h, params={"from": "2026-09-02", "to": "2026-09-01"}).status_code == 422


def test_audit_log_admin_only(sample_ctx):
    c = sample_ctx.client
    assert c.get("/api/audit", headers=sample_ctx.h["operator"]).status_code == 403
    rows = c.get("/api/audit?limit=5", headers=sample_ctx.h["admin"]).json()
    assert isinstance(rows, list)


def test_user_management_roles(fresh_sample_ctx):
    ctx = fresh_sample_ctx
    c, admin = ctx.client, ctx.h["admin"]
    u = c.post("/api/users", headers=admin, json={"username": "kim", "password": "pass1234", "role": "OPERATOR"}).json()
    assert u["role"] == "OPERATOR"
    assert c.post("/api/users", headers=ctx.h["operator"], json={"username": "x", "password": "pass1234"}).status_code == 403
    me = c.get("/api/auth/me", headers=admin).json()
    assert c.put(f"/api/users/{me['id']}", headers=admin, json={"role": "VIEWER"}).status_code == 409
    assert c.put(f"/api/users/{u['id']}", headers=admin, json={"is_active": False}).json()["is_active"] is False
    assert c.post("/api/auth/login", json={"username": "kim", "password": "pass1234"}).status_code == 401
    with ctx.db() as db:
        assert db.scalar(select(Lot).limit(1)) is not None


def test_lot_notes_search_and_filter(fresh_sample_ctx):
    ctx = fresh_sample_ctx
    c, h = ctx.client, ctx.h
    lot = ctx.scenario.planted_defect_lot_nos[0]
    assert c.post(f"/api/lots/{lot}/notes", headers=h["viewer"], json={"text": "x"}).status_code == 403
    n = c.post(f"/api/lots/{lot}/notes", headers=h["operator"], json={"text": "포장 박스 찌그러짐, 재검 요청"}).json()
    assert n["author"] == "operator"
    notes = c.get(f"/api/lots/{lot}/notes", headers=h["viewer"]).json()
    assert notes[0]["text"].startswith("포장")
    only = c.get("/api/lots?has_note=true", headers=h["viewer"]).json()
    assert [x["lot_no"] for x in only] == [lot] and only[0]["note_count"] == 1
    # 상단 검색: LOT 번호 일부, 메모 내용 둘 다
    by_no = c.get(f"/api/lots/search?q={lot[:9]}", headers=h["viewer"]).json()
    assert any(x["lot_no"] == lot for x in by_no)
    by_note = c.get("/api/lots/search?q=재검", headers=h["viewer"]).json()
    assert by_note[0]["lot_no"] == lot and by_note[0]["matched_note"]
    assert c.get(f"/api/lots/{lot}", headers=h["viewer"]).json()["last_note"].startswith("포장")
    # 삭제: 본인 또는 관리자만
    assert c.delete(f"/api/lots/notes/{n['id']}", headers=h["viewer"]).status_code == 403
    assert c.delete(f"/api/lots/notes/{n['id']}", headers=h["admin"]).status_code == 204
    assert c.get("/api/lots?has_note=true", headers=h["viewer"]).json() == []


def test_events_with_values(fresh_sample_ctx):
    r = fresh_sample_ctx.client.get("/api/events", headers=fresh_sample_ctx.h["viewer"], params={
        "from": "2026-10-09T00:00", "to": "2026-10-10T00:00", "types": "run_start", "values": True}).json()
    mix = [e for e in r["events"] if e["step"] == "혼합"]
    assert mix and "혼합 온도" in mix[0]["conditions"] and "out_of_range" in mix[0]["conditions"]["혼합 온도"]
    assert any(e["conditions"]["혼합 온도"]["out_of_range"] for e in mix)  # 설비 이상 기간


def test_notes_feed_groups_by_lot(fresh_sample_ctx):
    ctx = fresh_sample_ctx
    c, h = ctx.client, ctx.h
    a, b = ctx.scenario.planted_defect_lot_nos[:2]
    c.post(f"/api/lots/{a}/notes", headers=h["operator"], json={"text": "첫 메모"})
    c.post(f"/api/lots/{b}/notes", headers=h["operator"], json={"text": "다른 제품 메모"})
    c.post(f"/api/lots/{a}/notes", headers=h["operator"], json={"text": "두 번째 메모"})
    feed = c.get("/api/lots/notes/feed", headers=h["viewer"]).json()
    assert [g["lot_no"] for g in feed] == [a, b]  # 최근 메모 순
    assert [n["text"] for n in feed[0]["notes"]] == ["두 번째 메모", "첫 메모"]
    assert [g["lot_no"] for g in c.get("/api/lots/notes/feed?q=다른", headers=h["viewer"]).json()] == [b]
