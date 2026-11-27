from datetime import datetime, timedelta

import numpy as np

from app.core.causes.drivers import Row, analyze, matched_comparison
from app.core.defaults import DRIVER_ANALYSIS

T0 = datetime(2026, 1, 1, 8)


def _rows(temps, rates, ops=None, n=300):
    out = []
    for i, (t, r) in enumerate(zip(temps, rates)):
        cat = {"작업자": ops[i]} if ops else {}
        out.append(Row(T0 + timedelta(hours=i), n, round(r * n), {"온도": float(t)}, cat))
    return out


def test_insufficient_rows():
    r = analyze(_rows([1, 2, 3], [0.1, 0.1, 0.1]), cfg=DRIVER_ANALYSIS)
    assert r["status"] == "INSUFFICIENT" and "최소" in r["message"]


def test_no_signal_gives_no_primary():
    rng = np.random.default_rng(0)
    temps = rng.normal(50, 2, 20)
    r = analyze(_rows(temps, [0.02] * 20), cfg=DRIVER_ANALYSIS)
    assert r["verdict"]["primary"] is None and "찾지 못했습니다" in r["verdict"]["headline"]


def test_confounded_operator_is_explained():
    # 온도가 불량을 결정, B 작업자는 고온 구간에 몰려 있지만 같은 온도에서는 A 와 같다
    temps = [50, 51, 50, 52, 51, 50, 60, 61, 62, 60, 61, 62, 61, 50, 51]
    ops = ["A"] * 6 + ["B"] * 6 + ["A"] + ["A"] * 2
    temps[12] = 61  # A 가 고온에서 한 번
    rates = [0.01 if t < 55 else 0.10 for t in temps]
    r = analyze(_rows(temps, rates, ops), cfg=DRIVER_ANALYSIS)
    st = {f["name"]: f["status"] for f in r["factors"]}
    assert r["verdict"]["primary"] == "온도" and st["작업자"] == "EXPLAINED"


def test_matched_comparison_obs_exp():
    levels = ["A", "A", "B", "B"]
    primary = np.array([60.0, 61.0, 60.0, 61.0])
    k = np.array([10.0, 12.0, 10.0, 12.0])
    n = np.array([100.0] * 4)
    mc = matched_comparison(levels, primary, k, n, tol=1.0, expected=k.copy())
    assert mc["comparable"] and mc["max_rel_diff"] == 0
