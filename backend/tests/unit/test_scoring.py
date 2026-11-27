import pytest

from app.core.causes.scoring import (
    FACTORS,
    FactorResult,
    combine,
    design_mismatch,
    history_score,
    lift_score,
    lift_stats,
    spc_score,
)


def test_lift_stats_smoothing_and_fisher():
    st = lift_stats(8, 10, 10, 200, alpha=1, beta=20)
    assert st.rate_exposed == 0.8 and st.rate_other == 0.05
    assert st.raw_lift == pytest.approx(16)
    # 평활로 작은 표본의 극단값이 줄어듦
    assert 1 < st.smoothed_lift < st.raw_lift
    assert st.p_value < 1e-6


def test_small_sample_shrinks_toward_one():
    small = lift_stats(1, 1, 10, 200, 1, 20)
    big = lift_stats(10, 10, 10, 200, 1, 20)
    assert small.smoothed_lift < big.smoothed_lift
    assert small.p_value > 0.05


def test_lift_no_exposure():
    st = lift_stats(0, 0, 5, 100, 1, 20)
    assert st.smoothed_lift is None and lift_score(st, 4) == 0


def test_lift_score_saturates():
    st = lift_stats(50, 50, 0, 100, 1, 20)
    assert lift_score(st, 4.0) == 1.0
    neutral = lift_stats(5, 100, 5, 100, 1, 20)
    assert lift_score(neutral, 4.0) == pytest.approx(0, abs=1e-9)


def test_spc_and_history_scores():
    assert spc_score(0, 3) == 0 and spc_score(3, 3) == 1 and spc_score(5, 3) == 1
    assert history_score(0, 0, 0.3, 3) == 0
    assert history_score(3, 0, 0.3, 3) == 1
    assert history_score(0, 10, 0.3, 3) == 1
    assert history_score(1, 0, 0.3, 3) == pytest.approx(1 / 3)


def test_design_mismatch():
    expected = {"A": (0.6, 0.05), "B": (0.1, None), "C": (0.3, 0.05)}
    assert design_mismatch({"A": 0.6, "B": 0.1, "C": 0.3}, expected, 0.05) == []
    issues = design_mismatch({"A": 0.45, "B": 0.1, "C": 0.45}, expected, 0.05)
    assert {k for k, _ in issues} == {"A", "C"}
    issues = design_mismatch({"A": 0.6, "X": 0.4}, expected, 0.05)
    assert {k for k, _ in issues} == {"X", "B", "C"}


def test_combine_contributions_sum_to_total():
    factors = {k: FactorResult(0.5) for k in FACTORS}
    factors["lift"] = FactorResult(1.0)
    weights = {"spc": 1, "lift": 2, "arima": 0, "condition": 1, "design": 0, "history": 0}
    total, contrib = combine(factors, weights)
    assert total == pytest.approx((0.5 + 2 * 1.0 + 0.5) / 4)
    assert sum(contrib.values()) == pytest.approx(total)
    assert contrib["arima"] == 0
