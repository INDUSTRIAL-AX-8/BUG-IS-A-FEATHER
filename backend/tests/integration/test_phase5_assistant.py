"""Phase 5: 도구 호출 결과에 근거한 답변, JSON 대체 방식, Ollama 미연결 처리.

실제 Ollama 대신 httpx.MockTransport 로 만든 가짜 모델을 쓴다. 가짜 모델은
1) 질문 키워드로 도구를 고르고, 2) 도구 결과(JSON)에서 값을 꺼내 답을 만든다.
→ 답변 내용이 도구 결과에서 왔는지 검증할 수 있다.
"""

from __future__ import annotations

import json
import re

import httpx
import pytest

from app.llm import ollama_client


def _tool_for(question: str) -> tuple[str, dict]:
    m = re.search(r"LOT\s+(\S+)", question)
    if m:
        return "get_root_cause_candidates", {"lot_no": m.group(1)}
    if "주의" in question:
        return "get_watchlist", {}
    if "위반" in question:
        return "get_spc_violations", {"characteristic": "점도"}
    return "search_defect_history", {"period": "7d"}


def _answer_from(tool: str, result: dict) -> str:
    if "error" in result:
        return f"데이터에 없음: {result['error']}"
    if tool == "get_root_cause_candidates":
        c = result["candidates"][0]
        return f"{result['lot_no']} 의 1순위 원인 후보는 {c['name']} (점수 {c['score']}) 입니다. 후보이며 확정이 아닙니다."
    if tool == "get_watchlist":
        i = result["items"][0]
        return f"주의 부품 1순위: {i['part_or_step']} {i['characteristic']} 위험 {i['risk_score']}. 통계적 예측이며 확정 판단이 아닙니다."
    return json.dumps(result, ensure_ascii=False)[:200]


def _fake_ollama(tools_supported: bool = True):
    calls = {"chat": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen2.5:7b-instruct"}]})
        body = json.loads(request.content)
        calls["chat"] += 1
        msgs = body["messages"]
        assert msgs[0]["role"] == "system" and "데이터에 없음" in msgs[0]["content"]
        if "tools" in body and not tools_supported:
            return httpx.Response(400, json={"error": "registry.ollama.ai/x does not support tools"})
        question = next(m["content"] for m in reversed(msgs) if m["role"] == "user" and not m["content"].startswith("[도구"))
        tool, args = _tool_for(question)
        if tools_supported:
            last = msgs[-1]
            if last["role"] == "tool":
                return httpx.Response(200, json={"message": {"role": "assistant", "content": _answer_from(last["tool_name"], json.loads(last["content"]))}})
            return httpx.Response(200, json={"message": {"role": "assistant", "content": "",
                                                         "tool_calls": [{"function": {"name": tool, "arguments": args}}]}})
        # JSON 대체 방식
        last = msgs[-1]
        if last["role"] == "user" and last["content"].startswith("[도구"):
            name = re.match(r"\[도구 (\w+) 결과\]", last["content"]).group(1)
            result = json.loads(last["content"].split("\n", 1)[1].rsplit("\n", 1)[0])
            return httpx.Response(200, json={"message": {"role": "assistant", "content": json.dumps({"answer": _answer_from(name, result)}, ensure_ascii=False)}})
        return httpx.Response(200, json={"message": {"role": "assistant", "content": json.dumps({"tool": tool, "args": args})}})

    return httpx.MockTransport(handler), calls


@pytest.fixture()
def fake_llm(monkeypatch):
    def install(tools_supported=True):
        transport, calls = _fake_ollama(tools_supported)
        monkeypatch.setattr(ollama_client, "_transport_override", transport)
        return calls
    return install


def _ask(ctx, q):
    return ctx.client.post("/api/assistant/chat", headers=ctx.h["viewer"], json={"messages": [{"role": "user", "content": q}]})


def test_root_cause_question_answered_from_tool(sample_ctx, fake_llm):
    fake_llm()
    s = sample_ctx.scenario
    lot = s.planted_defect_lot_nos[0]
    r = _ask(sample_ctx, f"LOT {lot} 불량 원인 후보 알려줘")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "tools"
    call = body["tool_calls"][0]
    assert call["name"] == "get_root_cause_candidates" and call["ok"] and call["args"] == {"lot_no": lot}
    top = call["result"]["candidates"][0]["name"]
    assert top in body["answer"] and lot in body["answer"]  # 답변 값이 도구 결과에서 옴
    assert any(ref["kind"] == "lot" and ref["id"] == lot and ref["link"].startswith("/genealogy") for ref in body["refs"])


def test_watchlist_question(sample_ctx, fake_llm):
    fake_llm()
    sample_ctx.client.post("/api/forecast/run", headers=sample_ctx.h["operator"])
    r = _ask(sample_ctx, "이번 주 주의해야 할 부품 뭐야").json()
    assert r["tool_calls"][0]["name"] == "get_watchlist"
    first = r["tool_calls"][0]["result"]["items"][0]
    assert first["characteristic"] in r["answer"] and "확정 판단이 아닙니다" in r["answer"]
    assert any(ref["kind"] == "characteristic" for ref in r["refs"])


def test_json_fallback_when_tools_unsupported(sample_ctx, fake_llm):
    fake_llm(tools_supported=False)
    lot = sample_ctx.scenario.anomaly_defect_lot_nos[0]
    r = _ask(sample_ctx, f"LOT {lot} 불량 원인 후보 알려줘").json()
    assert r["mode"] == "json"
    assert r["tool_calls"][0]["name"] == "get_root_cause_candidates"
    assert sample_ctx.scenario.planted_equipment_name in r["answer"] or r["tool_calls"][0]["result"]["candidates"][0]["name"] in r["answer"]


def test_unknown_lot_reports_no_data(sample_ctx, fake_llm):
    fake_llm()
    r = _ask(sample_ctx, "LOT 없는LOT-999 불량 원인 후보 알려줘").json()
    assert r["tool_calls"][0]["ok"] is False and "데이터에 없음" in r["answer"]


def test_ollama_down_is_isolated(sample_ctx):
    # conftest 가 연결 불가 주소(127.0.0.1:9)를 설정 → 채팅만 503, 다른 기능은 정상
    h = sample_ctx.client.get("/api/assistant/health", headers=sample_ctx.h["viewer"]).json()
    assert h["available"] is False and "Ollama" in h["message"]
    r = _ask(sample_ctx, "LOT X 원인")
    assert r.status_code == 503 and "연결할 수 없습니다" in r.json()["message"]
    assert sample_ctx.client.get("/api/lots?limit=1", headers=sample_ctx.h["viewer"]).status_code == 200


def test_non_local_llm_address_refused():
    assert ollama_client.is_local_address("http://localhost:11434")
    assert ollama_client.is_local_address("http://127.0.0.1:11434")
    assert ollama_client.is_local_address("http://192.168.0.10:11434")
    assert not ollama_client.is_local_address("http://8.8.8.8:11434")


def test_tool_list_endpoint(sample_ctx):
    names = {t["name"] for t in sample_ctx.client.get("/api/assistant/tools", headers=sample_ctx.h["viewer"]).json()}
    assert names == {"get_lot", "trace_upstream", "trace_downstream", "get_root_cause_candidates",
                     "get_spc_violations", "get_watchlist", "search_defect_history", "get_process_drivers"}


def test_each_tool_runs_directly(sample_ctx):
    from app.llm.tools import call_tool

    s = sample_ctx.scenario
    with sample_ctx.db() as db:
        lot = s.planted_defect_lot_nos[0]
        assert call_tool(db, "get_lot", {"lot_no": lot})[0]["judgments"]
        up = call_tool(db, "trace_upstream", {"lot_no": lot, "depth": 3})[0]
        assert s.planted_raw_lot_no in {l["lot_no"] for l in up["upstream_lots"]}
        down = call_tool(db, "trace_downstream", {"node_id": s.planted_raw_lot_no})[0]
        assert down["already_defect"] >= len(s.planted_defect_lot_nos)
        eq = call_tool(db, "trace_downstream", {"node_id": f"eq:{s.planted_equipment_id}", "at": s.anomaly_from.isoformat()})[0]
        assert eq["window"] and eq["affected_finished"] > 0
        v = call_tool(db, "get_spc_violations", {"characteristic": "순도", "from": "2026-09-01"})[0]
        assert v["total"] >= 1
        h = call_tool(db, "search_defect_history", {"defect_type": "STR", "period": "2w"})[0]
        assert h["total"] > 0 and h["period"]["anchor"]
        h2 = call_tool(db, "search_defect_history", {"period": "2026-09-01~2026-09-30"})[0]
        assert h2["total"] >= 0
