"""업무 설정 기본값. DB Setting 테이블에 값이 없으면 이 값을 쓴다.

코드 곳곳에 숫자를 흩뿌리지 않고 이곳 한 군데에만 둔다.
"""

from __future__ import annotations

from typing import Any

# 넬슨룰 표준 파라미터. 현장에 따라 rule 2 를 7~8점으로 쓰는 등 조정 가능.
NELSON_RULES: dict[str, dict[str, Any]] = {
    "1": {"enabled": True, "params": {"sigma": 3.0}},
    "2": {"enabled": True, "params": {"count": 9}},
    "3": {"enabled": True, "params": {"count": 6}},
    "4": {"enabled": True, "params": {"count": 14}},
    "5": {"enabled": True, "params": {"window": 3, "min_points": 2, "sigma": 2.0}},
    "6": {"enabled": True, "params": {"window": 5, "min_points": 4, "sigma": 1.0}},
    "7": {"enabled": True, "params": {"count": 15, "sigma": 1.0}},
    "8": {"enabled": True, "params": {"count": 8, "sigma": 1.0}},
}

# 관리도 자동 선택 기준 (서브그룹 크기)
CONTROL_CHART: dict[str, Any] = {
    "imr_max_n": 1,  # n <= 1 → I-MR
    "xbar_r_max_n": 9,  # 2 <= n <= 9 → Xbar-R, 그 이상 → Xbar-S
    "min_baseline_subgroups": 20,
    "recompute_interval_minutes": 60,  # SPC 위반 재계산 주기 (스케줄러)
}

# 원인 후보 점수 가중치 (a)~(f)
CAUSE_WEIGHTS: dict[str, float] = {
    "spc": 1.0,
    "lift": 1.5,
    "arima": 0.5,
    "condition": 1.0,
    "design": 0.8,
    "history": 1.2,
}

CAUSE_SCORING: dict[str, Any] = {
    "analysis_window_days": 30,
    "downstream_window_days": 7,  # 설비/공정 노드 정방향 추적 기간 (±일)
    "beta_prior_alpha": 1.0,
    "beta_prior_beta": 20.0,
    "top_n_badges": 5,
    "max_trace_depth": 10,
    "lift_saturation": 4.0,  # 평활 Lift 가 이 값이면 (b) 점수 1.0
    "spc_rule_saturation": 3,  # 위반 룰 종류가 이 개수면 (a) 점수 1.0
    "history_saturation": 3.0,  # 가중 확정 횟수가 이 값이면 (f) 점수 1.0
    "history_type_weight": 0.3,  # 같은 ItemType(다른 LOT) 확정 1건의 가중치
    "design_ratio_tolerance": 0.05,  # 설계에 허용 오차가 없을 때 기본값
}

FORECAST: dict[str, Any] = {
    "min_points": 30,
    "horizon": 10,
    "max_p": 3,
    "max_d": 2,
    "max_q": 3,
    "adf_alpha": 0.05,
    "schedule_cron_hour": 2,
    "interval_alpha": 0.05,
}

TRACE: dict[str, Any] = {
    "default_depth": 6,
    "max_depth": 20,
    "max_nodes": 3000,
}

SETTING_DEFAULTS: dict[str, Any] = {
    "nelson_rules": NELSON_RULES,
    "control_chart": CONTROL_CHART,
    "cause_weights": CAUSE_WEIGHTS,
    "cause_scoring": CAUSE_SCORING,
    "forecast": FORECAST,
    "trace": TRACE,
}
