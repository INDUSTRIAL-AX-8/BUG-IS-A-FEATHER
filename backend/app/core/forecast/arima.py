"""ARIMA 추세 예측과 이탈 위험 점수 — 순수 함수 (DB/웹 무관).

절차
  1. 데이터가 min_points 미만이면 INSUFFICIENT (예측하지 않음)
  2. ADF 정상성 검정으로 d 후보를 좁힘: d=0 부터 차분하며 처음 정상성이 확인된 d 와 그 다음 d
  3. p ≤ max_p, q ≤ max_q 격자에서 AIC 최소 모형 선택 (적합 실패한 조합은 건너뜀)
  4. h 스텝 예측 평균과 예측 구간, 스텝별 이탈 확률 P(X > 상한) + P(X < 하한) 계산
  5. 위험 점수 = 스텝별 이탈 확률의 최댓값. 예상 이탈 시점 = 확률이 기준 이상이 되는 첫 스텝

어떤 경우에도 예외를 밖으로 던지지 않고 status/message 로 결과를 돌려준다.
"""

from __future__ import annotations

import math
import warnings
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
from scipy.stats import norm

D2_N2 = 1.128  # 이동범위(n=2)의 d2 상수

OK = "OK"
INSUFFICIENT = "INSUFFICIENT"
FAILED = "FAILED"


@dataclass
class ForecastOutcome:
    status: str
    message: str = ""
    order: tuple[int, int, int] | None = None
    aic: float | None = None
    mean: list[float] = field(default_factory=list)
    lower: list[float] = field(default_factory=list)
    upper: list[float] = field(default_factory=list)
    breach_prob: list[float] = field(default_factory=list)
    risk_score: float | None = None
    breach_step: int | None = None
    trend: str | None = None


def choose_d(values: np.ndarray, max_d: int, alpha: float) -> list[int]:
    from statsmodels.tsa.stattools import adfuller

    series = values
    for d in range(max_d + 1):
        if len(series) < 10 or np.allclose(series, series[0]):
            return [d]
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                p = adfuller(series, autolag="AIC")[1]
        except Exception:
            p = 1.0
        if p < alpha:
            return [d] if d == max_d else [d, d + 1]
        series = np.diff(series)
    return [max_d]


def _trend(values: np.ndarray, window: int, flat_sigma: float) -> str:
    tail = values[-window:]
    if len(tail) < 3:
        return "flat"
    x = np.arange(len(tail))
    slope = np.polyfit(x, tail, 1)[0]
    # 잡음 σ 는 이동범위로 추정 (추세가 있으면 전체 표준편차가 부풀려지므로)
    sd = float(np.mean(np.abs(np.diff(values)))) / D2_N2 or 1e-12
    change = slope * (len(tail) - 1)
    if abs(change) < flat_sigma * sd:
        return "flat"
    return "up" if change > 0 else "down"


def breach_probability(mean: float, se: float, upper: float | None, lower: float | None) -> float:
    if se <= 0 or not math.isfinite(se):
        return float((upper is not None and mean > upper) or (lower is not None and mean < lower))
    p = 0.0
    if upper is not None:
        p += float(norm.sf((upper - mean) / se))
    if lower is not None:
        p += float(norm.cdf((lower - mean) / se))
    return min(1.0, p)


def fit_forecast(
    values: Sequence[float],
    *,
    horizon: int,
    upper: float | None,
    lower: float | None,
    min_points: int = 30,
    max_p: int = 3,
    max_d: int = 2,
    max_q: int = 3,
    adf_alpha: float = 0.05,
    interval_alpha: float = 0.05,
    breach_threshold: float = 0.5,
    trend_window: int = 10,
    flat_threshold_sigma: float = 0.5,
) -> ForecastOutcome:
    y = np.asarray([v for v in values if v is not None and math.isfinite(v)], dtype=float)
    if len(y) < min_points:
        return ForecastOutcome(INSUFFICIENT, f"데이터 부족: {len(y)}개 (최소 {min_points}개 필요)")
    trend = _trend(y, trend_window, flat_threshold_sigma)
    if np.allclose(y, y[0]):
        return ForecastOutcome(FAILED, "값이 모두 같아 모형을 적합할 수 없습니다.", trend=trend)

    try:
        from statsmodels.tsa.arima.model import ARIMA
    except Exception as exc:  # pragma: no cover - 설치 문제
        return ForecastOutcome(FAILED, f"statsmodels 를 불러올 수 없습니다: {exc}", trend=trend)

    best = None
    errors = 0
    for d in choose_d(y, max_d, adf_alpha):
        trend_term = "c" if d == 0 else ("t" if d == 1 else "n")
        for p in range(max_p + 1):
            for q in range(max_q + 1):
                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore")
                        res = ARIMA(y, order=(p, d, q), trend=trend_term).fit()
                    aic = float(res.aic)
                    if not math.isfinite(aic):
                        continue
                    if best is None or aic < best[0]:
                        best = (aic, (p, d, q), res)
                except Exception:
                    errors += 1
                    continue
    if best is None:
        return ForecastOutcome(FAILED, f"모든 차수 조합에서 적합 실패 ({errors}건)", trend=trend)

    aic, order, res = best
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fc = res.get_forecast(horizon)
            mean = np.asarray(fc.predicted_mean, dtype=float)
            se = np.asarray(fc.se_mean, dtype=float)
            ci = np.asarray(fc.conf_int(alpha=interval_alpha), dtype=float)
    except Exception as exc:
        return ForecastOutcome(FAILED, f"예측 계산 실패: {exc}", order=order, aic=aic, trend=trend)

    probs = [breach_probability(m, s, upper, lower) for m, s in zip(mean, se)]
    breach_step = next((i + 1 for i, p in enumerate(probs) if p >= breach_threshold), None)
    return ForecastOutcome(
        OK, "", order, aic,
        mean=[float(x) for x in mean], lower=[float(x) for x in ci[:, 0]], upper=[float(x) for x in ci[:, 1]],
        breach_prob=[round(p, 6) for p in probs],
        risk_score=round(max(probs) if probs else 0.0, 6),
        breach_step=breach_step,
        trend=trend,
    )


def tighter_bounds(
    ucl: float | None, lcl: float | None, usl: float | None, lsl: float | None
) -> tuple[float | None, float | None]:
    """관리한계와 규격 중 더 안쪽 값을 경계로 쓴다 (먼저 닿는 쪽)."""
    uppers = [v for v in (ucl, usl) if v is not None]
    lowers = [v for v in (lcl, lsl) if v is not None]
    return (min(uppers) if uppers else None, max(lowers) if lowers else None)
