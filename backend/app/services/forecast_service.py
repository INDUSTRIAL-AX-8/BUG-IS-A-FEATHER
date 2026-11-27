"""ARIMA 배치 예측 조립과 주의 부품 순위."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from statistics import median

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.forecast.arima import fit_forecast, tighter_bounds
from app.errors import not_found
from app.models import Characteristic, ForecastResult, ForecastStatus, ItemType, ProcessStep
from app.services import spc_service
from app.services.settings_service import get_setting

log = logging.getLogger(__name__)


def _step_interval(times: list[datetime]) -> timedelta | None:
    if len(times) < 2:
        return None
    diffs = [(b - a).total_seconds() for a, b in zip(times, times[1:]) if b > a]
    return timedelta(seconds=median(diffs)) if diffs else None


def run_for_characteristic(db: Session, char: Characteristic, now: datetime | None = None) -> ForecastResult:
    cfg = get_setting(db, "forecast")
    now = now or datetime.now()
    try:
        state = spc_service.analyze(db, char)
        subgroups = state.subgroups[-cfg["max_points"]:]
        values = [sg.mean for sg in subgroups]
        lim = state.limits
        upper, lower = tighter_bounds(lim.ucl if lim else None, lim.lcl if lim else None, char.usl, char.lsl)
        out = fit_forecast(
            values, horizon=cfg["horizon"], upper=upper, lower=lower, min_points=cfg["min_points"],
            max_p=cfg["max_p"], max_d=cfg["max_d"], max_q=cfg["max_q"], adf_alpha=cfg["adf_alpha"],
            interval_alpha=cfg["interval_alpha"], breach_threshold=cfg["breach_probability"],
            trend_window=cfg["trend_window"], flat_threshold_sigma=cfg["flat_threshold_sigma"],
        )
        times = [sg.time for sg in subgroups]
        interval = _step_interval(times)
        index = [(times[-1] + interval * (i + 1)).isoformat() for i in range(len(out.mean))] if interval and times else []
        row = ForecastResult(
            characteristic_id=char.id, run_at=now, status=ForecastStatus(out.status), message=out.message or None,
            order_p=out.order[0] if out.order else None, order_d=out.order[1] if out.order else None,
            order_q=out.order[2] if out.order else None, aic=out.aic,
            forecast=None if out.status != "OK" else {
                "index": index, "mean": out.mean, "lower": out.lower, "upper": out.upper,
                "breach_prob": out.breach_prob, "bound_upper": upper, "bound_lower": lower,
            },
            risk_score=out.risk_score, expected_breach_step=out.breach_step,
            expected_breach_at=(times[-1] + interval * out.breach_step) if (out.breach_step and interval) else None,
            trend=out.trend,
        )
    except Exception as exc:  # 어떤 경우에도 배치를 멈추지 않음
        log.exception("예측 실패: 특성 %s", char.id)
        row = ForecastResult(characteristic_id=char.id, run_at=now, status=ForecastStatus.FAILED,
                             message=f"예측 중 오류: {exc}"[:500])
    db.add(row)
    db.commit()
    return row


def run_all(db: Session) -> dict[int, str]:
    now = datetime.now()
    result = {}
    for char in db.scalars(select(Characteristic).where(Characteristic.is_active.is_(True))):
        result[char.id] = run_for_characteristic(db, char, now).status.value
    return result


def _latest_rows(db: Session) -> list[ForecastResult]:
    latest = (
        select(ForecastResult.characteristic_id, func.max(ForecastResult.id).label("mid"))
        .group_by(ForecastResult.characteristic_id).subquery()
    )
    return list(db.scalars(select(ForecastResult).join(latest, latest.c.mid == ForecastResult.id)))


def watchlist(db: Session) -> list[dict]:
    items = dict(db.execute(select(ItemType.id, ItemType.name)).all())
    steps = dict(db.execute(select(ProcessStep.id, ProcessStep.name)).all())
    chars = {c.id: c for c in db.scalars(select(Characteristic))}
    out = []
    for r in _latest_rows(db):
        c = chars.get(r.characteristic_id)
        if c is None or not c.is_active:
            continue
        out.append({
            "characteristic_id": c.id, "characteristic": c.name, "unit": c.unit,
            "item_type": items.get(c.item_type_id) if c.item_type_id else None,
            "process_step": steps.get(c.process_step_id) if c.process_step_id else None,
            "status": r.status.value, "message": r.message, "risk_score": r.risk_score,
            "expected_breach_at": r.expected_breach_at, "expected_breach_step": r.expected_breach_step,
            "trend": r.trend, "order": [r.order_p, r.order_d, r.order_q] if r.order_p is not None else None,
            "run_at": r.run_at,
        })
    rank = {"OK": 0, "INSUFFICIENT": 1, "FAILED": 2}
    out.sort(key=lambda x: (rank[x["status"]], -(x["risk_score"] or 0)))
    return out


def detail(db: Session, char_id: int) -> dict:
    char = db.get(Characteristic, char_id)
    if char is None:
        raise not_found("측정 특성")
    state = spc_service.analyze(db, char)
    row = db.scalar(select(ForecastResult).where(ForecastResult.characteristic_id == char_id)
                    .order_by(ForecastResult.id.desc()).limit(1))
    max_points = get_setting(db, "forecast")["max_points"]
    sgs = state.subgroups[-max_points:]
    lim = state.limits
    return {
        "characteristic": {"id": char.id, "name": char.name, "unit": char.unit, "usl": char.usl,
                           "lsl": char.lsl, "target": char.target},
        "limits": None if lim is None else {"center": lim.center, "ucl": lim.ucl, "lcl": lim.lcl,
                                            "provisional": state.provisional},
        "actual": [{"time": sg.time, "value": sg.mean} for sg in sgs],
        "forecast": None if row is None else {
            "status": row.status.value, "message": row.message, "run_at": row.run_at,
            "order": [row.order_p, row.order_d, row.order_q] if row.order_p is not None else None,
            "aic": row.aic, "risk_score": row.risk_score, "expected_breach_at": row.expected_breach_at,
            "trend": row.trend, "series": row.forecast,
        },
    }
