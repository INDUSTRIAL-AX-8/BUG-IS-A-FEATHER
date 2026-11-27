"""APScheduler 배치: SPC 위반 재계산, ARIMA 예측 (단일 프로세스 기준 — uvicorn workers=1)."""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app.db import SessionLocal
from app.services.settings_service import get_setting

log = logging.getLogger(__name__)


def job_spc() -> None:
    from app.services.spc_service import recompute_all

    with SessionLocal() as db:
        try:
            result = recompute_all(db)
            log.info("SPC 재계산 완료: 특성 %d개", len(result))
        except Exception:  # 배치 실패가 서버를 죽이지 않게
            log.exception("SPC 재계산 실패")


def job_forecast() -> None:
    try:
        from app.services.forecast_service import run_all
    except ModuleNotFoundError:
        return
    with SessionLocal() as db:
        try:
            run_all(db)
        except Exception:
            log.exception("ARIMA 배치 예측 실패")


def job_drivers() -> None:
    from app.services.driver_service import analyze_all

    with SessionLocal() as db:
        try:
            analyze_all(db)
        except Exception:
            log.exception("요인 분석 배치 실패")


def start_scheduler() -> BackgroundScheduler:
    with SessionLocal() as db:
        cc = get_setting(db, "control_chart")
        fc = get_setting(db, "forecast")
    sched = BackgroundScheduler(timezone=None)
    sched.add_job(job_spc, "interval", minutes=cc["recompute_interval_minutes"], id="spc",
                  max_instances=1, coalesce=True)
    sched.add_job(job_forecast, "cron", hour=fc["schedule_cron_hour"], id="forecast",
                  max_instances=1, coalesce=True)
    sched.add_job(job_drivers, "cron", hour=fc["schedule_cron_hour"], minute=30, id="drivers",
                  max_instances=1, coalesce=True)
    sched.start()
    return sched
