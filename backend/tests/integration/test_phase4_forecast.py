"""Phase 4: 추세를 심은 특성이 주의 부품 상위, 데이터 부족/적합 실패 무오류."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import Characteristic, ForecastResult, ItemType, Lot, Measurement


@pytest.fixture(scope="module")
def forecasted(sample_ctx):
    r = sample_ctx.client.post("/api/forecast/run", headers=sample_ctx.h["operator"])
    assert r.status_code == 200, r.text
    return sample_ctx


def test_trend_characteristic_tops_watchlist(forecasted):
    s = forecasted.scenario
    data = forecasted.client.get("/api/forecast/watchlist", headers=forecasted.h["viewer"]).json()
    assert "확정 판단이 아닙니다" in data["disclaimer"]
    items = data["items"]
    assert items[0]["characteristic_id"] == s.trend_characteristic_id
    top = items[0]
    assert top["status"] == "OK" and top["trend"] == "up" and top["risk_score"] > 0.5
    assert top["expected_breach_at"] is not None and top["item_type"]


def test_forecast_detail_series(forecasted):
    s = forecasted.scenario
    d = forecasted.client.get(f"/api/forecast/{s.trend_characteristic_id}", headers=forecasted.h["viewer"]).json()
    series = d["forecast"]["series"]
    assert len(series["mean"]) == len(series["index"]) == 10
    assert len(d["actual"]) >= 30


def test_insufficient_and_failure_are_recorded_not_raised(fresh_sample_ctx):
    ctx = fresh_sample_ctx
    admin = ctx.h["admin"]
    it = ctx.client.post("/api/item-types", json={"name": "신규 부품", "category": "PART"}, headers=admin).json()
    few = ctx.client.post("/api/characteristics", json={"name": "적은 데이터", "item_type_id": it["id"]}, headers=admin).json()
    const = ctx.client.post("/api/characteristics", json={"name": "상수", "item_type_id": it["id"]}, headers=admin).json()
    with ctx.db() as db:
        base = datetime(2026, 9, 1)
        lot = Lot(lot_no="NEW-1", item_type_id=it["id"], created_at=base)
        db.add(lot)
        db.flush()
        db.add_all([Measurement(characteristic_id=few["id"], lot_id=lot.id, measured_at=base + timedelta(hours=i), value=1.0 + i * 0.01) for i in range(5)])
        db.add_all([Measurement(characteristic_id=const["id"], lot_id=lot.id, measured_at=base + timedelta(hours=i), value=3.0) for i in range(40)])
        db.commit()
    r = ctx.client.post("/api/forecast/run", headers=ctx.h["operator"])
    assert r.status_code == 200
    res = r.json()["results"]
    assert res[str(few["id"])] == "INSUFFICIENT"
    assert res[str(const["id"])] == "FAILED"
    items = ctx.client.get("/api/forecast/watchlist", headers=ctx.h["viewer"]).json()["items"]
    msgs = {i["characteristic"]: i for i in items}
    assert "데이터 부족" in msgs["적은 데이터"]["message"]
    assert items[-1]["status"] in ("INSUFFICIENT", "FAILED")  # 실패/부족은 순위 뒤로


def test_arima_feeds_cause_factor(forecasted):
    s = forecasted.scenario
    with forecasted.db() as db:
        b_type = db.scalar(select(Characteristic.item_type_id).where(Characteristic.id == s.trend_characteristic_id))
        assert db.scalar(select(ForecastResult).where(ForecastResult.characteristic_id == s.trend_characteristic_id))
    data = forecasted.client.get(f"/api/causes/{s.planted_defect_lot_nos[0]}", headers=forecasted.h["viewer"]).json()
    b_lots = [c for c in data["candidates"] if c["type"] == "LOT" and c["sub"].startswith("원자재-B")]
    assert b_lots and b_lots[0]["factors"]["arima"]["score"] > 0.5
    # 원자재 LOT 원인은 여전히 Top-3
    assert s.planted_raw_lot_no in [c["label"] for c in data["candidates"][:3]]
    assert b_type is not None and ItemType is not None
