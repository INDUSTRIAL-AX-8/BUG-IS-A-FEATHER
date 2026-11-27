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
    "min_regime_subgroups": 30,  # 체제별 기준선: 이보다 적은 체제는 전체 기준선 사용 (KAMP min_ref)
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
    "common_share_threshold": 0.9,  # 완제품의 이 비율 이상이 거치는 노드는 '공통 경로'로 보고 점수 감쇠
}

FORECAST: dict[str, Any] = {
    "min_points": 30,
    "horizon": 10,
    "max_p": 3,
    "max_d": 2,
    "max_q": 3,
    "adf_alpha": 0.05,
    "schedule_cron_hour": 2,
    "interval_alpha": 0.05,  # 예측 구간 95%
    "max_points": 300,  # 적합에 쓰는 최근 포인트 수 (속도)
    "breach_probability": 0.5,  # 이 확률 이상이면 '예상 이탈 시점'
    "trend_window": 10,  # 추세 방향 판단에 쓰는 최근 포인트 수
    "flat_threshold_sigma": 0.5,  # 창 전체 변화가 σ의 이 배수 미만이면 '평탄'
}

TRACE: dict[str, Any] = {
    "default_depth": 6,
    "max_depth": 20,
    "max_nodes": 3000,
}

# 요인 분석(집계 공정 기록·LOT 단위 공통)
DRIVER_ANALYSIS: dict[str, Any] = {
    "min_rows": 8,  # 분석에 필요한 최소 행(시간·run) 수
    "alpha": 0.05,  # 유의수준
    "max_model_terms": 3,  # 다변량 모형에 넣을 최대 요인 수
    "collinearity_r": 0.95,  # 이 이상 상관이면 같은 정보로 보고 분리 불가 처리
    "explained_shrink": 0.5,  # 보정 후 효과가 이 비율 이상 줄면 '다른 요인으로 설명됨'
    "onset_baseline_rows": 4,  # 변화 시작점 판단 기준 구간(처음 n행)
    "onset_sigma": 3.0,  # 기준 구간 평균 + kσ 를 넘으면 변화 시작
    "event_window_rows": 3,  # 설비 이벤트 전후 비교 행 수
    "similar_tolerance_sigma": 0.5,  # 교란 확인: 주요인 값이 이 σ 안에서 같은 행끼리 비교
    "matched_rel_diff": 0.25,  # 같은 조건 비교에서 수준 간 불량률 차이가 이 비율 이하면 '설명됨'
    "discrete_levels": 3,  # 고유값이 이 개수 이하인 연속 요인(설정값 등)도 수준으로 비교
    "min_deviance_gain": 0.03,  # 다변량에 넣으려면 설명력(이탈도)을 이 비율 이상 더해야 함 (실질 유의성)
    "min_type_rows": 3,  # 불량 유형-요인 연관을 볼 최소 행 수
    "learn_ranges": True,  # 유의한 연속 요인의 학습 범위를 조건 항목에 기록
}

SETTING_DEFAULTS: dict[str, Any] = {
    "nelson_rules": NELSON_RULES,
    "control_chart": CONTROL_CHART,
    "cause_weights": CAUSE_WEIGHTS,
    "cause_scoring": CAUSE_SCORING,
    "forecast": FORECAST,
    "trace": TRACE,
    "driver_analysis": DRIVER_ANALYSIS,
    "dataset_notes": {},  # 설비 이름 → 데이터 주의사항 목록 (가져오기 시 기록)
    # 화면 용어 프리셋 (general | injection | diecasting) + 개별 덮어쓰기. 현장 도메인 용어로 화면을 바꾼다
    "terminology": {"preset": "general", "overrides": {}},
}
