"""실시간 수집(시연) 제어와 SSE 스트림. 화면은 /api/live/stream 을 EventSource 로 구독한다."""

from __future__ import annotations

import asyncio
import json
import queue
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.deps import admin, operator, viewer
from app.config import get_settings
from app.db import get_db
from app.errors import AppError
from app.live.simulator import SCENARIOS, get_simulator
from app.models import User
from app.services import audit
from app.services.simulated_service import purge_simulated

router = APIRouter(prefix="/live", tags=["실시간 수집"])
HEARTBEAT_SEC = 15


class StartIn(BaseModel):
    equipment_id: int | None = None
    scenario: str = Field(default="drift")
    # 배속: 실제 1초에 진행하는 기록 시간(초). 1 = 실시간. 지정하면 실제 시계(지금 시각) 기준으로 기록이 쌓인다
    speed: float | None = Field(default=None, ge=1, le=3600)
    # 시험용: speed 없이 interval_sec 만 주면 마지막 기록 뒤로 이 간격마다 1구간
    interval_sec: float | None = Field(default=None, ge=0.2, le=3600)


def _json(o):
    return o.isoformat() if isinstance(o, datetime) else str(o)


@router.get("/status")
def status(_=Depends(viewer)):
    return get_simulator().status()


@router.post("/start")
def start(body: StartIn, _: User = Depends(operator)):
    if not get_settings().live_simulator_enabled:
        raise AppError(403, "이 서버에서는 실시간 수집(시연)이 꺼져 있습니다. 시연 기록은 실제 기록과 같은 DB 에 쌓이므로 "
                            "시연용 서버에서만 켜세요 (설정 [live] simulator_enabled).")
    if body.scenario not in SCENARIOS:
        raise AppError(422, f"시나리오는 {', '.join(SCENARIOS)} 중 하나여야 합니다.")
    try:
        speed = body.speed if body.speed is not None or body.interval_sec is not None else 1.0
        return get_simulator().start(body.equipment_id, body.scenario, interval_sec=body.interval_sec, speed=speed)
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


@router.delete("/records")
def purge_records(db: Session = Depends(get_db), user: User = Depends(admin)):
    """시연이 만든 기록을 모두 지운다 (실제 기록은 그대로). 지운 뒤 관리도 위반을 다시 계산한다."""
    if get_simulator().state.running:
        raise AppError(409, "실시간 수집이 켜져 있습니다. 먼저 정지한 뒤 지우세요.")
    counts = purge_simulated(db)
    audit.record(db, user, "delete", "simulated_records", None, after=counts)
    db.commit()
    return counts


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
