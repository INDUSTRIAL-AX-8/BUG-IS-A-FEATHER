"""ARIMA 주의 부품 API. 통계적 예측이며 확정 판단이 아니다."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth.deps import operator, viewer
from app.db import get_db
from app.errors import not_found
from app.models import Characteristic
from app.services import forecast_service

router = APIRouter(prefix="/forecast", tags=["ARIMA 예측"])

DISCLAIMER = "통계적 예측이며 확정 판단이 아닙니다."


@router.get("/watchlist")
def get_watchlist(db: Session = Depends(get_db), _=Depends(viewer)):
    return {"disclaimer": DISCLAIMER, "items": forecast_service.watchlist(db)}


@router.get("/{char_id}")
def get_detail(char_id: int, db: Session = Depends(get_db), _=Depends(viewer)):
    return {"disclaimer": DISCLAIMER, **forecast_service.detail(db, char_id)}


@router.post("/run")
def run(char_id: int | None = None, db: Session = Depends(get_db), _=Depends(operator)):
    if char_id is not None:
        char = db.get(Characteristic, char_id)
        if char is None:
            raise not_found("측정 특성")
        row = forecast_service.run_for_characteristic(db, char)
        return {"results": {char_id: row.status.value}}
    return {"results": forecast_service.run_all(db)}
