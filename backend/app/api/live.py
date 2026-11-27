"""실시간 수집(시연) 제어와 SSE 스트림. 화면은 /api/live/stream 을 EventSource 로 구독한다."""

from __future__ import annotations

import asyncio
import json
import queue
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.auth.deps import operator, viewer
from app.errors import AppError
from app.live.simulator import SCENARIOS, get_simulator
from app.models import User

router = APIRouter(prefix="/live", tags=["실시간 수집"])
HEARTBEAT_SEC = 15


class StartIn(BaseModel):
    equipment_id: int | None = None
    scenario: str = Field(default="drift")
    interval_sec: float = Field(default=2.0, ge=0.2, le=60)


def _json(o):
    return o.isoformat() if isinstance(o, datetime) else str(o)


@router.get("/status")
def status(_=Depends(viewer)):
    return get_simulator().status()


@router.post("/start")
def start(body: StartIn, _: User = Depends(operator)):
    if body.scenario not in SCENARIOS:
        raise AppError(422, f"시나리오는 {', '.join(SCENARIOS)} 중 하나여야 합니다.")
    try:
        return get_simulator().start(body.equipment_id, body.scenario, body.interval_sec)
    except ValueError as exc:
        raise AppError(422, str(exc)) from exc


@router.post("/stop")
def stop(_: User = Depends(operator)):
    return get_simulator().stop()


@router.post("/maintenance")
def maintenance(_: User = Depends(operator)):
    """다음 틱에 정비(정지) 이벤트를 넣고 값을 정상으로 되돌린다."""
    sim = get_simulator()
    if not sim.state.running:
        raise AppError(409, "실시간 수집이 꺼져 있습니다.")
    sim.request_maintenance()
    return {"ok": True}


@router.get("/stream")
async def stream(request: Request, limit: int | None = Query(default=None, ge=1, le=1000), _=Depends(viewer)):
    """Server-Sent Events. limit 개를 보내면 종료(테스트용), 없으면 연결이 끊길 때까지."""
    sim = get_simulator()
    q = sim.subscribe()

    async def gen():
        sent = 0
        idle = 0.0
        try:
            yield "retry: 3000\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = q.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(0.25)
                    idle += 0.25
                    if idle >= HEARTBEAT_SEC:
                        idle = 0.0
                        yield ": heartbeat\n\n"
                    continue
                idle = 0.0
                yield f"event: {msg.get('type', 'message')}\ndata: {json.dumps(msg, ensure_ascii=False, default=_json)}\n\n"
                sent += 1
                if limit and sent >= limit:
                    break
        finally:
            sim.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
