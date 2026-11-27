"""어시스턴트 오케스트레이터: 질문 → (도구 호출 반복) → 근거 기반 한국어 답변.

- 모델이 tool calling 을 지원하면 Ollama tools 사용
- 지원하지 않으면 JSON 응답 방식으로 대체: 모델이 {"tool":..., "args":...} 또는 {"answer":...} 를 반환
- 도구 호출 내역(이름, 인자, 결과 요약, 오류)과 근거 refs 를 응답에 그대로 담아 화면에서 펼쳐 볼 수 있게 한다
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.errors import AppError
from app.llm.ollama_client import OllamaClient, ToolsUnsupported
from app.llm.tools import TOOL_SCHEMAS, call_tool

MAX_ROUNDS = 5
MAX_HISTORY = 10
MAX_TOOL_CHARS = 6000

SYSTEM_PROMPT = """당신은 제조 현장의 불량 원인 추적 시스템 어시스턴트다. 항상 한국어로 답한다.

규칙:
1. 데이터는 반드시 제공된 도구로만 조회한다. 직접 계산하거나 수치를 지어내지 않는다.
2. 도구 결과에 없는 내용은 "데이터에 없음"이라고 답한다. 추측으로 원인을 단정하지 않는다.
3. 원인 후보는 "후보"이며 확정이 아님을 분명히 한다. 점수와 근거(요소별 근거 문장)를 함께 말한다.
4. 답변에 근거가 된 LOT 번호, 위반 ID(위반 #번호), 판정 ID(판정 #번호)를 그대로 적는다.
5. 예측(ARIMA) 결과는 "통계적 예측이며 확정 판단이 아님"을 밝힌다.
6. 짧고 명확하게, 실무자가 바로 행동할 수 있게 답한다. 필요하면 다음 확인 항목을 제안한다.
7. 형식: 일반 문장과 '- ' 목록만 쓴다. 마크다운 표, 굵게(**), 제목(#), 이모지는 쓰지 않는다.
"""

FALLBACK_PROMPT = """
이 모델은 도구 호출 기능이 없으므로 JSON 으로만 답한다. 형식은 둘 중 하나다:
{"tool": "<도구 이름>", "args": {...}}   ← 데이터가 필요할 때 (한 번에 하나)
{"answer": "<최종 한국어 답변>"}          ← 도구 결과로 충분할 때
사용 가능한 도구:
""" + "\n".join(
    f"- {t['function']['name']}({', '.join(t['function']['parameters']['properties'])}): {t['function']['description']}"
    for t in TOOL_SCHEMAS
)


def _trim(obj: Any) -> str:
    text = json.dumps(obj, ensure_ascii=False, default=str)
    return text if len(text) <= MAX_TOOL_CHARS else text[:MAX_TOOL_CHARS] + " …(생략)"


def _run_tool(db: Session, name: str, args: Any, log: list, refs: dict) -> str:
    started = datetime.now()
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {}
    try:
        result, rs = call_tool(db, name, args or {})
        for r in rs:
            refs[(r["kind"], str(r["id"]))] = r
        log.append({"name": name, "args": args, "ok": True, "result": result,
                    "ms": int((datetime.now() - started).total_seconds() * 1000)})
        return _trim(result)
    except AppError as exc:
        log.append({"name": name, "args": args, "ok": False, "error": exc.message})
        return _trim({"error": exc.message})
    except Exception as exc:  # 도구 오류가 대화를 끊지 않게
        log.append({"name": name, "args": args, "ok": False, "error": f"도구 실행 오류: {exc.__class__.__name__}"})
        return _trim({"error": "도구 실행 중 오류가 발생했습니다."})


def _history(messages: list[dict]) -> list[dict]:
    out = []
    for m in messages[-MAX_HISTORY:]:
        if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str):
            out.append({"role": m["role"], "content": m["content"][:4000]})
    return out


def ask(db: Session, messages: list[dict], client: OllamaClient | None = None) -> dict[str, Any]:
    client = client or OllamaClient()
    history = _history(messages)
    if not history or history[-1]["role"] != "user":
        raise AppError(422, "마지막 메시지는 사용자 질문이어야 합니다.")
    log: list[dict] = []
    refs: dict = {}
    try:
        answer = _ask_with_tools(db, client, history, log, refs)
        mode = "tools"
    except ToolsUnsupported:
        answer = _ask_with_json(db, client, history, log, refs)
        mode = "json"
    return {"answer": answer, "tool_calls": log, "refs": list(refs.values()), "model": client.model, "mode": mode}


def _ask_with_tools(db, client: OllamaClient, history, log, refs) -> str:
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}, *history]
    for _ in range(MAX_ROUNDS):
        msg = client.chat(msgs, tools=TOOL_SCHEMAS)
        calls = msg.get("tool_calls") or []
        if not calls:
            return (msg.get("content") or "").strip() or "데이터에 없음"
        msgs.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
        for call in calls:
            fn = call.get("function", {})
            content = _run_tool(db, fn.get("name", ""), fn.get("arguments") or {}, log, refs)
            msgs.append({"role": "tool", "content": content, "tool_name": fn.get("name", "")})
    return "도구 호출 횟수 한도에 도달했습니다. 질문을 더 구체적으로 해 주세요."


def _ask_with_json(db, client: OllamaClient, history, log, refs) -> str:
    msgs = [{"role": "system", "content": SYSTEM_PROMPT + FALLBACK_PROMPT}, *history]
    for _ in range(MAX_ROUNDS):
        msg = client.chat(msgs, json_mode=True)
        raw = (msg.get("content") or "").strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return raw or "데이터에 없음"
        if isinstance(data, dict) and "answer" in data:
            return str(data["answer"]).strip() or "데이터에 없음"
        if isinstance(data, dict) and data.get("tool"):
            content = _run_tool(db, str(data["tool"]), data.get("args") or {}, log, refs)
            msgs.append({"role": "assistant", "content": raw})
            msgs.append({"role": "user", "content": f"[도구 {data['tool']} 결과]\n{content}\n위 결과만 근거로 계속하라."})
            continue
        return raw
    return "도구 호출 횟수 한도에 도달했습니다. 질문을 더 구체적으로 해 주세요."
