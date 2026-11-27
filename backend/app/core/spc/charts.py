"""관리도 계산 — 순수 함수. I-MR, Xbar-R, Xbar-S.

관리도 상수(d2, D3, D4)는 표준 SPC 표 값이며, c4 는 감마함수로 정확히 계산한다.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from statistics import fmean, stdev

from app.models.enums import ChartType

# n: d2  (서브그룹 범위의 기대값 / σ)
D2 = {
    2: 1.128, 3: 1.693, 4: 2.059, 5: 2.326, 6: 2.534, 7: 2.704, 8: 2.847, 9: 2.970, 10: 3.078,
}
# n: (D3, D4)  범위 관리도 한계 계수
D3_D4 = {
    2: (0.0, 3.267), 3: (0.0, 2.574), 4: (0.0, 2.282), 5: (0.0, 2.114), 6: (0.0, 2.004),
    7: (0.076, 1.924), 8: (0.136, 1.864), 9: (0.184, 1.816), 10: (0.223, 1.777),
}
SIGMA_K = 3.0  # 관리한계 = 중심 ± 3σ (관리도 정의)


def c4(n: int) -> float:
    return math.sqrt(2.0 / (n - 1)) * math.exp(math.lgamma(n / 2) - math.lgamma((n - 1) / 2))


def choose_chart(subgroup_size: int, requested: ChartType, imr_max_n: int = 1, xbar_r_max_n: int = 9) -> ChartType:
    if requested != ChartType.AUTO:
        return requested
    if subgroup_size <= imr_max_n:
        return ChartType.IMR
    if subgroup_size <= xbar_r_max_n:
        return ChartType.XBAR_R
    return ChartType.XBAR_S


@dataclass
class Subgroup:
    key: int  # 서브그룹 번호 (없으면 측정 ID)
    time: object  # 서브그룹 첫 측정 시각
    values: list[float]
    lot_ids: list[int] = field(default_factory=list)
    measurement_ids: list[int] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.values)

    @property
    def mean(self) -> float:
        return fmean(self.values)

    @property
    def range(self) -> float:
        return max(self.values) - min(self.values)

    @property
    def std(self) -> float:
        return stdev(self.values) if len(self.values) > 1 else 0.0


@dataclass
class Limits:
    chart_type: ChartType
    center: float
    sigma: float  # 공정(개별값) σ 추정치
    ucl: float  # 대표 서브그룹 크기 기준
    lcl: float
    nominal_n: int
    dispersion_center: float | None
    dispersion_ucl: float | None
    dispersion_lcl: float | None
    n_subgroups: int

    def point_sigma(self, n: int) -> float:
        """해당 포인트 통계량의 σ (I 관리도는 σ, 평균 관리도는 σ/√n)."""
        if self.chart_type == ChartType.IMR:
            return self.sigma
        return self.sigma / math.sqrt(max(n, 1))


class ChartError(ValueError):
    pass


def compute_limits(subgroups: Sequence[Subgroup], chart_type: ChartType, nominal_n: int) -> Limits:
    if chart_type == ChartType.IMR:
        xs = [v for sg in subgroups for v in sg.values]
        if len(xs) < 2:
            raise ChartError("관리한계를 계산하려면 측정값이 2개 이상 필요합니다.")
        mr = [abs(xs[i] - xs[i - 1]) for i in range(1, len(xs))]
        mr_bar = fmean(mr)
        sigma = mr_bar / D2[2]
        center = fmean(xs)
        d3, d4 = D3_D4[2]
        return Limits(chart_type, center, sigma, center + SIGMA_K * sigma, center - SIGMA_K * sigma,
                      1, mr_bar, d4 * mr_bar, d3 * mr_bar, len(xs))

    usable = [sg for sg in subgroups if sg.n >= 2]
    if len(usable) < 2:
        raise ChartError("관리한계를 계산하려면 크기 2 이상의 서브그룹이 2개 이상 필요합니다.")
    center = fmean(sg.mean for sg in usable)
    n = nominal_n

    if chart_type == ChartType.XBAR_R:
        if n not in D2:
            raise ChartError("Xbar-R 관리도는 서브그룹 크기 2~10 에서만 사용할 수 있습니다. Xbar-S 를 선택하세요.")
        # 크기가 다른 서브그룹은 각자 d2 로 σ 추정 후 평균
        sigma = fmean(sg.range / D2[min(sg.n, 10)] for sg in usable)
        r_bar = sigma * D2[n]
        d3, d4 = D3_D4[n]
        disp = (r_bar, d4 * r_bar, d3 * r_bar)
    elif chart_type == ChartType.XBAR_S:
        sigma = fmean(sg.std / c4(sg.n) for sg in usable)
        s_bar = sigma * c4(n)
        c = c4(n)
        b_factor = SIGMA_K * math.sqrt(1 - c * c) / c
        disp = (s_bar, (1 + b_factor) * s_bar, max(0.0, 1 - b_factor) * s_bar)
    else:
        raise ChartError(f"지원하지 않는 관리도 종류: {chart_type}")

    half = SIGMA_K * sigma / math.sqrt(n)
    return Limits(chart_type, center, sigma, center + half, center - half, n, *disp, len(usable))


@dataclass
class ChartPoint:
    index: int
    subgroup: Subgroup
    value: float  # 개별값 또는 평균
    dispersion: float | None  # MR / R / S
    z: float
    ucl: float
    lcl: float


def chart_points(subgroups: Sequence[Subgroup], limits: Limits) -> list[ChartPoint]:
    points: list[ChartPoint] = []
    if limits.chart_type == ChartType.IMR:
        flat = [(sg, v) for sg in subgroups for v in sg.values]
        prev = None
        for i, (sg, v) in enumerate(flat):
            s = limits.point_sigma(1)
            z = (v - limits.center) / s if s > 0 else 0.0
            single = Subgroup(sg.key, sg.time, [v], sg.lot_ids, sg.measurement_ids)
            points.append(ChartPoint(i, single, v, None if prev is None else abs(v - prev), z,
                                     limits.ucl, limits.lcl))
            prev = v
        return points

    for i, sg in enumerate(subgroups):
        s = limits.point_sigma(sg.n)
        z = (sg.mean - limits.center) / s if s > 0 else 0.0
        disp = sg.range if limits.chart_type == ChartType.XBAR_R else sg.std
        points.append(ChartPoint(i, sg, sg.mean, disp if sg.n > 1 else None, z,
                                 limits.center + SIGMA_K * s, limits.center - SIGMA_K * s))
    return points
