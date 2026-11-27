import numpy as np
import pytest

from app.core.forecast.arima import (
    FAILED,
    INSUFFICIENT,
    OK,
    breach_probability,
    choose_d,
    fit_forecast,
    tighter_bounds,
)

FAST = dict(max_p=1, max_q=1)


def test_insufficient_data_is_not_an_error():
    out = fit_forecast([1.0] * 10, horizon=5, upper=2, lower=0, min_points=30)
    assert out.status == INSUFFICIENT and "30" in out.message


def test_constant_series_reports_failure_without_raising():
    out = fit_forecast([5.0] * 40, horizon=5, upper=6, lower=4)
    assert out.status == FAILED


def test_nan_values_are_dropped():
    vals = [float("nan")] * 25 + list(np.random.default_rng(0).normal(0, 1, 10))
    assert fit_forecast(vals, horizon=3, upper=3, lower=-3).status == INSUFFICIENT


def test_trend_series_high_risk_and_breach_step():
    rng = np.random.default_rng(1)
    y = 0.45 + 0.005 * np.arange(60) + rng.normal(0, 0.02, 60)
    out = fit_forecast(y, horizon=10, upper=0.76, lower=0.2, **FAST)  # 끝값 ≈0.745, 기울기 0.005
    assert out.status == OK
    assert out.trend == "up"
    assert out.risk_score > 0.5 and out.breach_step is not None
    assert len(out.mean) == len(out.lower) == len(out.upper) == 10
    assert all(lo <= m <= hi for lo, m, hi in zip(out.lower, out.mean, out.upper))


def test_stationary_series_low_risk():
    rng = np.random.default_rng(2)
    y = rng.normal(100, 1, 80)
    out = fit_forecast(y, horizon=10, upper=106, lower=94, **FAST)
    assert out.status == OK and out.risk_score < 0.05 and out.breach_step is None
    assert out.order[1] == 0


def test_choose_d_random_walk_needs_differencing():
    rng = np.random.default_rng(3)
    rw = np.cumsum(rng.normal(0, 1, 200))
    assert choose_d(rw, 2, 0.05)[0] >= 1
    assert choose_d(rng.normal(0, 1, 200), 2, 0.05)[0] == 0


def test_breach_probability():
    assert breach_probability(0, 1, 1.96, -1.96) == pytest.approx(0.05, abs=1e-3)
    assert breach_probability(0, 1, None, None) == 0
    assert breach_probability(5, 0, 3, None) == 1


def test_tighter_bounds():
    assert tighter_bounds(10, 2, 12, 1) == (10, 2)
    assert tighter_bounds(None, None, 12, None) == (12, None)
