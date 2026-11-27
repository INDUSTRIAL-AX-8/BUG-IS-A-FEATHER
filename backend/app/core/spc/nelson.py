"""넬슨룰 8종 — 순수 함수.

입력:
  values: 관리도에 찍히는 통계량 (개별값 또는 서브그룹 평균)
  z:      (값 - 중심선) / 해당 포인트의 σ  (서브그룹 크기가 달라도 포인트별 σ 로 정규화)
  params: 룰 파라미터 (core.defaults.NELSON_RULES 참고)

각 룰은 [(끝 인덱스, [위반 포인트 인덱스...]), ...] 를 돌려준다.
evaluate() 는 켜진 룰만 실행하고, 같은 룰에서 이어지거나 겹치는 발생을 하나의 위반으로 묶는다.

룰 8 은 넬슨 원 정의대로 "연속 n점이 모두 ±1σ 밖이고 중심선 양쪽에 걸쳐 있음" 으로 판정한다.
(한쪽에만 몰린 경우는 룰 2/5/6 이 잡는다.)
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

Hit = tuple[int, list[int]]

RULE_DESCRIPTIONS: dict[int, str] = {
    1: "1점이 {sigma}σ 밖",
    2: "연속 {count}점이 중심선 한쪽",
    3: "연속 {count}점이 계속 증가 또는 감소",
    4: "연속 {count}점이 번갈아 증감",
    5: "연속 {window}점 중 {min_points}점이 같은 쪽 {sigma}σ 밖",
    6: "연속 {window}점 중 {min_points}점이 같은 쪽 {sigma}σ 밖",
    7: "연속 {count}점이 ±{sigma}σ 안",
    8: "연속 {count}점이 ±{sigma}σ 밖 (양쪽에 걸침)",
}

PARAM_SPEC: dict[int, dict[str, tuple[type, float, float]]] = {
    # 이름: (타입, 최소, 최대)
    1: {"sigma": (float, 0.5, 6)},
    2: {"count": (int, 2, 50)},
    3: {"count": (int, 3, 50)},
    4: {"count": (int, 4, 50)},
    5: {"window": (int, 2, 20), "min_points": (int, 1, 20), "sigma": (float, 0.5, 6)},
    6: {"window": (int, 2, 20), "min_points": (int, 1, 20), "sigma": (float, 0.5, 6)},
    7: {"count": (int, 2, 100), "sigma": (float, 0.1, 6)},
    8: {"count": (int, 2, 100), "sigma": (float, 0.1, 6)},
}


def describe(rule_no: int, params: dict[str, Any]) -> str:
    def fmt(v):
        return f"{v:g}" if isinstance(v, float) else str(v)

    return RULE_DESCRIPTIONS[rule_no].format(**{k: fmt(v) for k, v in params.items()})


def rule1(values: Sequence[float], z: Sequence[float], p: dict) -> list[Hit]:
    k = p["sigma"]
    return [(i, [i]) for i, zi in enumerate(z) if abs(zi) > k]


def _runs(flags: Sequence[int], count: int) -> list[Hit]:
    """flags[i] 가 같은 0 아닌 값으로 count 개 이상 이어질 때, 끝 인덱스마다 Hit."""
    hits: list[Hit] = []
    run = 0
    for i, f in enumerate(flags):
        run = run + 1 if f != 0 and i > 0 and flags[i - 1] == f else (1 if f != 0 else 0)
        if run >= count:
            hits.append((i, list(range(i - count + 1, i + 1))))
    return hits


def rule2(values, z, p) -> list[Hit]:
    side = [1 if zi > 0 else (-1 if zi < 0 else 0) for zi in z]
    return _runs(side, p["count"])


def rule3(values, z, p) -> list[Hit]:
    count = p["count"]
    diffs = [0] + [
        (1 if values[i] > values[i - 1] else -1 if values[i] < values[i - 1] else 0)
        for i in range(1, len(values))
    ]
    hits: list[Hit] = []
    run = 0  # 같은 방향 diff 가 이어진 개수
    for i in range(1, len(values)):
        d = diffs[i]
        run = run + 1 if d != 0 and d == diffs[i - 1] else (1 if d != 0 else 0)
        if run >= count - 1:
            hits.append((i, list(range(i - count + 1, i + 1))))
    return hits


def rule4(values, z, p) -> list[Hit]:
    count = p["count"]
    diffs = [0] + [
        (1 if values[i] > values[i - 1] else -1 if values[i] < values[i - 1] else 0)
        for i in range(1, len(values))
    ]
    hits: list[Hit] = []
    run = 0  # 번갈아 바뀐 diff 개수
    for i in range(1, len(values)):
        d = diffs[i]
        if d == 0:
            run = 0
        elif run > 0 and d == -diffs[i - 1]:
            run += 1
        else:
            run = 1
        if run >= count - 1:
            hits.append((i, list(range(i - count + 1, i + 1))))
    return hits


def _k_of_m(z, window: int, min_points: int, k: float) -> list[Hit]:
    hits: list[Hit] = []
    for i in range(len(z)):
        for side in (1, -1):
            if side * z[i] <= k:
                continue  # 끝 포인트 자신이 그 쪽 kσ 밖일 때만 (중복 발생 방지)
            lo = max(0, i - window + 1)
            pts = [j for j in range(lo, i + 1) if side * z[j] > k]
            if len(pts) >= min_points:
                hits.append((i, pts))
    return hits


def rule5(values, z, p) -> list[Hit]:
    return _k_of_m(z, p["window"], p["min_points"], p["sigma"])


def rule6(values, z, p) -> list[Hit]:
    return _k_of_m(z, p["window"], p["min_points"], p["sigma"])


def rule7(values, z, p) -> list[Hit]:
    k, count = p["sigma"], p["count"]
    flags = [1 if abs(zi) < k else 0 for zi in z]
    return _runs(flags, count)


def rule8(values, z, p) -> list[Hit]:
    k, count = p["sigma"], p["count"]
    hits: list[Hit] = []
    run = 0
    for i, zi in enumerate(z):
        run = run + 1 if abs(zi) > k else 0
        if run >= count:
            window = range(i - count + 1, i + 1)
            if any(z[j] > 0 for j in window) and any(z[j] < 0 for j in window):
                hits.append((i, list(window)))
    return hits


RULES: dict[int, Callable[[Sequence[float], Sequence[float], dict], list[Hit]]] = {
    1: rule1, 2: rule2, 3: rule3, 4: rule4, 5: rule5, 6: rule6, 7: rule7, 8: rule8,
}


@dataclass
class Violation:
    rule_no: int
    first_index: int  # 처음 룰이 성립한 포인트 (발생 시점)
    last_index: int
    points: list[int] = field(default_factory=list)


def group_hits(rule_no: int, hits: list[Hit]) -> list[Violation]:
    """겹치거나 바로 이어지는 발생을 하나로 묶는다 (긴 런이 여러 건으로 쪼개지지 않게)."""
    out: list[Violation] = []
    for end, pts in sorted(hits):
        if out and min(pts) <= out[-1].last_index + 1:
            v = out[-1]
            v.last_index = max(v.last_index, end)
            v.points = sorted(set(v.points) | set(pts))
        else:
            out.append(Violation(rule_no, end, end, sorted(pts)))
    return out


def merge_config(defaults: dict[str, dict], override: dict[str, dict] | None) -> dict[int, dict]:
    """전역 기본값 + 특성별 설정 → {룰번호: {"enabled": bool, "params": {...}}}"""
    merged: dict[int, dict] = {}
    for key, base in defaults.items():
        rule = {"enabled": base.get("enabled", True), "params": dict(base.get("params", {}))}
        o = (override or {}).get(key)
        if o:
            if "enabled" in o:
                rule["enabled"] = bool(o["enabled"])
            rule["params"].update(o.get("params") or {})
        merged[int(key)] = rule
    return merged


def validate_config(config: dict[str, dict]) -> list[str]:
    """룰 설정 검증. 오류 메시지 목록(한국어)을 돌려준다."""
    errors: list[str] = []
    for key, rule in config.items():
        if not str(key).isdigit() or int(key) not in PARAM_SPEC:
            errors.append(f"룰 번호 {key} 는 1~8 이어야 합니다.")
            continue
        spec = PARAM_SPEC[int(key)]
        for name, value in (rule.get("params") or {}).items():
            if name not in spec:
                errors.append(f"룰 {key}: 알 수 없는 파라미터 '{name}'")
                continue
            typ, lo, hi = spec[name]
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                errors.append(f"룰 {key}: '{name}' 는 숫자여야 합니다.")
            elif typ is int and float(value) != int(value):
                errors.append(f"룰 {key}: '{name}' 는 정수여야 합니다.")
            elif not lo <= value <= hi:
                errors.append(f"룰 {key}: '{name}' 는 {lo:g}~{hi:g} 범위여야 합니다.")
        params = rule.get("params") or {}
        if "window" in params and "min_points" in params and params["min_points"] > params["window"]:
            errors.append(f"룰 {key}: min_points 가 window 보다 클 수 없습니다.")
    return errors


def evaluate(
    values: Sequence[float], z: Sequence[float], config: dict[int, dict]
) -> list[Violation]:
    if len(values) != len(z):
        raise ValueError("values 와 z 길이가 다릅니다.")
    result: list[Violation] = []
    for rule_no, rule in sorted(config.items()):
        if not rule.get("enabled", True):
            continue
        params = rule["params"]
        typed = {
            name: (int(v) if PARAM_SPEC[rule_no].get(name, (float,))[0] is int else float(v))
            for name, v in params.items()
        }
        result.extend(group_hits(rule_no, RULES[rule_no](values, z, typed)))
    return result
