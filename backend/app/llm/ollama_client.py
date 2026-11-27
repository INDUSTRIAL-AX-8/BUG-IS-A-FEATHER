"""로컬 Ollama HTTP 클라이언트. 사내망/로컬 주소만 허용한다 (공정 데이터가 서버 밖으로 나가지 않게)."""

from __future__ import annotations

import ipaddress
import socket
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import get_settings

# 테스트에서 가짜 Ollama 를 끼우기 위한 transport (운영에서는 None)
_transport_override: httpx.BaseTransport | None = None


class LlmUnavailable(Exception):
    """Ollama 에 연결할 수 없음 (꺼짐, 주소 오류, 모델 없음)."""


class ToolsUnsupported(Exception):
    """모델이 tool calling 을 지원하지 않음 → JSON 응답 방식으로 대체."""


def is_local_address(url: str) -> bool:
    """루프백 또는 사설망 주소만 허용."""
    host = urlparse(url).hostname
    if not host:
        return False
    if host in ("localhost",):
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not (ip.is_loopback or ip.is_private or ip.is_link_local):
            return False
    return True


class OllamaClient:
    def __init__(self) -> None:
        s = get_settings()
        self.base_url = s.llm_ollama_url.rstrip("/")
        self.model = s.llm_model
        self.temperature = s.llm_temperature
        self.num_ctx = s.llm_num_ctx
        self.timeout = s.llm_timeout_seconds

    def _client(self) -> httpx.Client:
        if not is_local_address(self.base_url):
            raise LlmUnavailable(
                f"LLM 주소 '{self.base_url}' 는 로컬/사내망 주소가 아닙니다. 공정 데이터 보호를 위해 로컬 Ollama 만 사용합니다."
            )
        return httpx.Client(base_url=self.base_url, timeout=self.timeout, transport=_transport_override)

    def health(self) -> dict[str, Any]:
        try:
            with self._client() as c:
                r = c.get("/api/tags", timeout=5.0)
                r.raise_for_status()
                names = [m.get("name", "") for m in r.json().get("models", [])]
        except LlmUnavailable as exc:
            return {"available": False, "model": self.model, "model_present": False, "message": str(exc)}
        except (httpx.HTTPError, ValueError):
            return {"available": False, "model": self.model, "model_present": False,
                    "message": f"Ollama({self.base_url})에 연결할 수 없습니다. Ollama 가 실행 중인지 확인하세요."}
        present = any(n == self.model or n.split(":")[0] == self.model for n in names)
        msg = None if present else f"모델 '{self.model}' 이(가) 설치되어 있지 않습니다. 설치된 모델: {', '.join(names) or '없음'}"
        return {"available": True, "model": self.model, "model_present": present, "models": names, "message": msg}

    def chat(self, messages: list[dict[str, Any]], tools: list[dict] | None = None,
             json_mode: bool = False) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": self.temperature, "num_ctx": self.num_ctx},
        }
        if tools:
            body["tools"] = tools
        if json_mode:
            body["format"] = "json"
        try:
            with self._client() as c:
                r = c.post("/api/chat", json=body)
        except httpx.HTTPError as exc:
            raise LlmUnavailable(f"Ollama({self.base_url})에 연결할 수 없습니다: {exc.__class__.__name__}") from exc
        if r.status_code >= 400:
            text = r.text
            if tools and "does not support tools" in text:
                raise ToolsUnsupported(text)
            if "not found" in text and "model" in text:
                raise LlmUnavailable(f"모델 '{self.model}' 을(를) 찾을 수 없습니다. 모델 파일을 반입·설치했는지 확인하세요.")
            raise LlmUnavailable(f"Ollama 오류 ({r.status_code}): {text[:200]}")
        return r.json().get("message", {})
