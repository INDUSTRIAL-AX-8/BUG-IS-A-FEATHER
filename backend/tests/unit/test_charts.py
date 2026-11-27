import math

import numpy as np
import pytest

from app.core.spc.charts import ChartError, Subgroup, c4, chart_points, choose_chart, compute_limits
from app.models.enums import ChartType


def _subgroups(data):
    return [Subgroup(i, i, list(map(float, v))) for i, v in enumerate(data)]


def test_choose_chart_auto_and_manual():
    assert choose_chart(1, ChartType.AUTO) == ChartType.IMR
    assert choose_chart(5, ChartType.AUTO) == ChartType.XBAR_R
    assert choose_chart(9, ChartType.AUTO) == ChartType.XBAR_R
    assert choose_chart(10, ChartType.AUTO) == ChartType.XBAR_S
    assert choose_chart(5, ChartType.XBAR_S) == ChartType.XBAR_S


def test_c4_known_values():
    assert c4(2) == pytest.approx(0.7979, abs=1e-4)
    assert c4(10) == pytest.approx(0.9727, abs=1e-4)


def test_imr_limits():
    lim = compute_limits(_subgroups([[10], [12], [11], [13]]), ChartType.IMR, 1)
    mr_bar = (2 + 1 + 2) / 3
    assert lim.center == pytest.approx(11.5)
    assert lim.sigma == pytest.approx(mr_bar / 1.128)
    assert lim.dispersion_ucl == pytest.approx(3.267 * mr_bar)


def test_xbar_r_limits_match_a2_formula():
    data = [[10, 11, 12, 11, 10], [11, 12, 13, 12, 11], [9, 10, 11, 10, 9]]
    lim = compute_limits(_subgroups(data), ChartType.XBAR_R, 5)
    r_bar = 2.0
    a2 = 3 / (2.326 * math.sqrt(5))
    assert lim.ucl - lim.center == pytest.approx(a2 * r_bar, rel=1e-3)  # A2(5)=0.577
    assert lim.dispersion_ucl == pytest.approx(2.114 * r_bar)


def test_xbar_s_estimates_sigma():
    rng = np.random.default_rng(0)
    data = rng.normal(100, 2.0, size=(200, 12)).tolist()
    lim = compute_limits(_subgroups(data), ChartType.XBAR_S, 12)
    assert lim.sigma == pytest.approx(2.0, rel=0.05)
    assert lim.center == pytest.approx(100, abs=0.2)


def test_limits_errors_korean():
    with pytest.raises(ChartError, match="2개 이상"):
        compute_limits(_subgroups([[1]]), ChartType.IMR, 1)
    with pytest.raises(ChartError, match="Xbar-S"):
        compute_limits(_subgroups([[1] * 12, [2] * 12]), ChartType.XBAR_R, 12)


def test_chart_points_z_and_moving_range():
    sgs = _subgroups([[10], [12], [11], [13]])
    lim = compute_limits(sgs, ChartType.IMR, 1)
    pts = chart_points(sgs, lim)
    assert pts[0].dispersion is None and pts[1].dispersion == 2
    assert pts[0].z == pytest.approx((10 - 11.5) / lim.sigma)


def test_variable_subgroup_size_widens_limits():
    sgs = _subgroups([[1, 2, 3, 2, 1], [2, 3, 4, 3, 2], [1, 2]])
    lim = compute_limits(sgs, ChartType.XBAR_R, 5)
    pts = chart_points(sgs, lim)
    assert pts[2].ucl - pts[2].lcl > pts[0].ucl - pts[0].lcl
