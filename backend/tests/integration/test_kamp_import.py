"""KAMP 다이캐스팅 임포터: 형식 호환 합성 픽스처로 매핑 검증 + (선택) 실데이터 수치 재현."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.auth.provider import create_user
from app.models import (
    BaselineScope,
    Characteristic,
    ConditionDefinition,
    DefectJudgment,
    GenealogyEdge,
    Lot,
    LotStatus,
    Role,
)
from app.sample.kamp_casting import WARMUP_COND, import_kamp
from tests.conftest import Ctx, _client_for, _login, _make_session_factory

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "kamp_mini"


@pytest.fixture(scope="module")
def kamp_ctx():
    factory = _make_session_factory()
    with factory() as db:
        create_user(db, "admin", "admin1234", Role.ADMIN)
        db.commit()
        summary = import_kamp(db, FIXTURE)
    client = _client_for(factory)
    headers = {"viewer": _login(client, "viewer", "viewer1234"), "admin": _login(client, "admin", "admin1234")}
    return Ctx(factory, client, headers, summary)


def test_mapping_lots_ids_and_genealogy(kamp_ctx):
    s = kamp_ctx.scenario
    assert s.runs == 2 and s.shots == 85
    assert s.lots == 2 + 85 + 170  # 용탕 + shot + 캐비티 2
    with kamp_ctx.db() as db:
        assert db.scalar(select(Lot.lot_no).where(Lot.lot_no == "20260302A0004-1"))
        assert db.scalar(select(Lot.lot_no).where(Lot.lot_no == "20260303B0040-2"))
        assert db.scalar(select(Lot.lot_no).where(Lot.lot_no == "M20260302A"))
        assert db.scalar(select(func.count()).select_from(GenealogyEdge)) == 85 + 170


def test_judgments_from_cavity_labels(kamp_ctx):
    with kamp_ctx.db() as db:
        n = db.scalar(select(func.count()).select_from(DefectJudgment))
        defect_lots = db.scalar(select(func.count()).select_from(Lot).where(Lot.status == LotStatus.DEFECT))
        memo = db.scalar(select(DefectJudgment.memo).limit(1))
    assert n == kamp_ctx.scenario.judgments == defect_lots > 0
    assert "KAMP 품질 라벨" in memo


def test_stable_window_and_warmup_as_conditions(kamp_ctx):
    with kamp_ctx.db() as db:
        cyc = db.scalar(select(ConditionDefinition).where(ConditionDefinition.name == "사이클 시간"))
        warm = db.scalar(select(ConditionDefinition).where(ConditionDefinition.name == WARMUP_COND))
        chars = db.scalars(select(Characteristic)).all()
    assert (cyc.min_value, cyc.max_value) == (21.0, 34.0)
    assert (warm.min_value, warm.max_value) == (0, 0)
    assert all(c.baseline_scope == BaselineScope.PARENT_LOT for c in chars)
    runs = kamp_ctx.client.get("/api/lots/20260302A0001/runs", headers=kamp_ctx.h["viewer"]).json()
    conds = {c["name"]: c for c in runs[0]["conditions"]}
    assert conds[WARMUP_COND]["out_of_range"] is True  # 가생산 shot


def test_per_run_baseline_chart(kamp_ctx):
    with kamp_ctx.db() as db:
        cid = db.scalar(select(Characteristic.id).where(Characteristic.name == "주조 압력"))
    data = kamp_ctx.client.get(f"/api/spc/{cid}/chart", headers=kamp_ctx.h["viewer"]).json()
    assert data["baseline_scope"] == "PARENT_LOT"
    assert len({p["regime"] for p in data["points"]}) == 2
    assert len({round(p["center"], 6) for p in data["points"]}) == 2  # 런별 중심선
    # 체제별 기준선은 고정 불가
    r = kamp_ctx.client.post(f"/api/spc/{cid}/limits", headers=kamp_ctx.h["admin"],
                             json={"baseline_from": "2026-03-01T00:00", "baseline_to": "2026-03-04T00:00"})
    assert r.status_code == 409


def test_cause_analysis_points_to_out_of_window_condition(kamp_ctx):
    # 사이클 시간이 창 밖(가생산)인 shot 의 캐비티-1 불량 → 사이클 시간 조건이 상위 후보
    c, h = kamp_ctx.client, kamp_ctx.h["viewer"]
    lot = "20260302A0001-1"
    assert c.get(f"/api/lots/{lot}", headers=h).json()["status"] == "DEFECT"
    data = c.get(f"/api/causes/{lot}", headers=h).json()
    top = [x["label"] for x in data["candidates"][:3]]
    assert "사이클 시간" in top or WARMUP_COND in top, top
    machine = next(x for x in data["candidates"] if x["label"] == "다이캐스팅기-1")
    assert "specificity" in machine


@pytest.mark.skipif(not os.environ.get("BIF_KAMP_RAW_DIR"), reason="실데이터 경로(BIF_KAMP_RAW_DIR) 미지정")
def test_real_kamp_data_reproduces_report_numbers():
    factory = _make_session_factory()
    with factory() as db:
        s = import_kamp(db, Path(os.environ["BIF_KAMP_RAW_DIR"]), run_analysis=False)
    # KAMP 보고서(docs/company_scenario.md): 런 9, shot 5,161, 폐기 캐비티 1,144, 캐비티 1 17.0% / 2 7.8%
    assert (s.runs, s.shots, s.judgments) == (9, 5161, 1144)
    assert round(s.defect_rate_cavity[1], 3) == 0.170 and round(s.defect_rate_cavity[2], 3) == 0.078
