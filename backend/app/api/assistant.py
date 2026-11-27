"""로컬 LLM 어시스턴트 API. Ollama 가 꺼져 있어도 다른 기능에는 영향이 없다."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.deps import viewer
from app.db import get_db
from app.errors import AppError
from app.llm import assistant
from app.llm.ollama_client import LlmUnavailable, OllamaClient
from app.llm.tools import TOOL_SCHEMAS

router = APIRouter(prefix="/assistant", tags=["어시스턴트"])


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)


class ChatIn(BaseModel):
    messages: list[Message] = Field(min_length=1, max_length=40)


@router.get("/health")
def health(_=Depends(viewer)):
    return OllamaClient().health()


@router.get("/tools")
def tools(_=Depends(viewer)):
    return [{"name": t["function"]["name"], "description": t["function"]["description"],
             "parameters": list(t["function"]["parameters"]["properties"])} for t in TOOL_SCHEMAS]


@router.post("/chat")
def chat(body: ChatIn, db: Session = Depends(get_db), _=Depends(viewer)):
    try:
        return assistant.ask(db, [m.model_dump() for m in body.messages])
    except LlmUnavailable as exc:
        raise AppError(503, str(exc)) from exc
