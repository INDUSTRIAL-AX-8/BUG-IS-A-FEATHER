"""원인 후보 점수 — 순수 함수. 블랙박스 없이 요소별 원점수/정규화 점수/기여도를 그대로 노출한다.

요소
  spc       (a) 해당 노드 관련 특성값의 넬슨룰 위반 룰 종류 수
  lift      (b) 노드를 거친 제품 vs 거치지 않은 제품의 불량률 (Beta 평활 Lift, Fisher p-value 병기)
  arima     (c) 관련 특성의 ARIMA 위험 점수 (최댓값)
  condition (d) 작업 조건 허용 범위 이탈 비율
  design    (e) 설계(ProductDesign)와 실제 투입/공정이 다른가
  history   (f) 같은 노드/같은 ItemType 이 같은 불량 유형의 원인으로 확정된 이력

종합 점수 = Σ wᵢ·sᵢ / Σ wᵢ  (0~1). 기여도 = wᵢ·sᵢ / Σ wᵢ.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from scipy.stats import fisher_exact

FACTORS = ("spc", "lift", "arima", "condition", "design", "history")
FACTOR_LABELS = {
    "spc": "SPC 신호",
    "lift": "공유 불량률",
    "arima": "ARIMA 위험",
    "condition": "작업 조건 이탈",
    "design": "설계 불일치",
    "history": "과거 확정 이력",
}


def clamp01(x: float) -> float:
    return 0.0 if x != x else max(0.0, min(1.0, x))  # NaN → 0


@dataclass
class LiftStats:
    exposed_n: int
    exposed_defects: int
    other_n: int
    other_defects: int
    rate_exposed: float | None
    rate_other: float | None
    raw_lift: float | None
    smoothed_lift: float | None
    p_value: float | None


def lift_stats(
    exposed_defects: int, exposed_n: int, other_defects: int, other_n: int, alpha: float, beta: float
) -> LiftStats:
    """Beta(alpha, beta) 사전분포로 평활한 불량률 비와 Fisher 정확 검정(단측, 노출군이 더 큼) p-value."""
    if exposed_n <= 0:
        return LiftStats(0, 0, other_n, other_defects, None, None, None, None, None)
    rate_e = exposed_defects / exposed_n
    rate_o = other_defects / other_n if other_n > 0 else None
    raw = (rate_e / rate_o) if rate_o else None
    sm_e = (exposed_defects + alpha) / (exposed_n + alpha + beta)
    sm_o = (other_defects + alpha) / (other_n + alpha + beta)
    smoothed = sm_e / sm_o
    p = None
    if other_n > 0:
        table = [[exposed_defects, exposed_n - exposed_defects], [other_defects, other_n - other_defects]]
        p = float(fisher_exact(table, alternative="greater")[1])
    return LiftStats(exposed_n, exposed_defects, other_n, other_defects, rate_e, rate_o, raw, smoothed, p)


def lift_score(stats: LiftStats, saturation: float) -> float:
    if stats.smoothed_lift is None or saturation <= 1:
        return 0.0
    return clamp01((stats.smoothed_lift - 1.0) / (saturation - 1.0))


def spc_score(rule_kinds: int, saturation: int) -> float:
    return clamp01(rule_kinds / saturation) if saturation > 0 else 0.0


def history_score(same_node: float, same_type: float, type_weight: float, saturation: float) -> float:
    return clamp01((same_node + type_weight * same_type) / saturation) if saturation > 0 else 0.0


def design_mismatch(
    actual_ratios: dict[Any, float],
    expected: dict[Any, tuple[float | None, float | None]],
    default_tolerance: float,
) -> list[tuple[Any, str]]:
    """실제 투입 비율 vs 설계. 반환: [(관련 ItemType 키, 설명)]

    expected: {ItemType: (비율 또는 None, 허용오차 또는 None)}
    """
    issues: list[tuple[Any, str]] = []
    for key, ratio in actual_ratios.items():
        if key not in expected:
            issues.append((key, "설계에 없는 종류가 투입됨"))
    for key, (ratio, tol) in expected.items():
        if key not in actual_ratios:
            issues.append((key, "설계에 있는 종류가 투입되지 않음"))
            continue
        if ratio is None:
            continue
        t = default_tolerance if tol is None else tol
        diff = actual_ratios[key] - ratio
        if abs(diff) > t + 1e-9:
            issues.append((key, f"투입 비율 {actual_ratios[key]:.3f} (설계 {ratio:.3f} ± {t:.3f})"))
    return issues


@dataclass
class FactorResult:
    score: float  # 0~1 정규화
    raw: dict[str, Any] = field(default_factory=dict)  # 설명용 원점수
    note: str = ""


def combine(factors: dict[str, FactorResult], weights: dict[str, float]) -> tuple[float, dict[str, float]]:
    total_w = sum(max(0.0, weights.get(k, 0.0)) for k in FACTORS)
    if total_w <= 0:
        return 0.0, {k: 0.0 for k in FACTORS}
    contrib = {
        k: max(0.0, weights.get(k, 0.0)) * factors[k].score / total_w if k in factors else 0.0
        for k in FACTORS
    }
    return sum(contrib.values()), contrib
