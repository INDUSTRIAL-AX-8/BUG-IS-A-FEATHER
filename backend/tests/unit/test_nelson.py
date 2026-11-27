"""넬슨룰 룰별 위반/비위반 시퀀스 단위 테스트."""

import pytest

from app.core.defaults import NELSON_RULES
from app.core.spc.nelson import (
    RULES,
    describe,
    evaluate,
    group_hits,
    merge_config,
    validate_config,
)

P = {int(k): v["params"] for k, v in NELSON_RULES.items()}


def hits(rule_no, z, params=None, values=None):
    values = z if values is None else values
    return RULES[rule_no](values, z, params or P[rule_no])


def only(rule_no, z, params=None):
    """해당 룰만 켠 evaluate 결과."""
    cfg = merge_config(NELSON_RULES, {str(r): {"enabled": r == rule_no} for r in range(1, 9)})
    if params:
        cfg[rule_no]["params"].update(params)
    return evaluate(z, z, cfg)


# ---- Rule 1
def test_rule1_violation():
    z = [0.1, -0.5, 3.2, 0.0, -3.01]
    assert [h[0] for h in hits(1, z)] == [2, 4]


def test_rule1_no_violation_on_boundary():
    assert hits(1, [3.0, -3.0, 2.9, 0]) == []


# ---- Rule 2
def test_rule2_violation_nine_same_side():
    z = [-1] + [0.3] * 9
    v = only(2, z)
    assert len(v) == 1 and v[0].first_index == 9 and v[0].points == list(range(1, 10))


def test_rule2_no_violation_eight_points_or_zero_break():
    assert hits(2, [0.3] * 8) == []
    assert hits(2, [0.3] * 4 + [0.0] + [0.3] * 4) == []


def test_rule2_param_change_to_seven():
    assert hits(2, [0.3] * 7, {"count": 7}) != []
    assert hits(2, [0.3] * 7) == []


def test_rule2_long_run_grouped_as_one():
    assert len(only(2, [0.5] * 20)) == 1


# ---- Rule 3
def test_rule3_violation_increasing_and_decreasing():
    assert hits(3, [1, 2, 3, 4, 5, 6])[0] == (5, [0, 1, 2, 3, 4, 5])
    assert hits(3, [6, 5, 4, 3, 2, 1]) != []


def test_rule3_no_violation():
    assert hits(3, [1, 2, 3, 4, 5]) == []  # 5점
    assert hits(3, [1, 2, 3, 3, 4, 5, 6]) == []  # 같은 값은 끊김
    assert hits(3, [1, 2, 3, 2, 3, 4, 5]) == []


# ---- Rule 4
def test_rule4_violation_alternating():
    z = [(1 if i % 2 else -1) * 0.5 for i in range(14)]
    assert hits(4, z) != []


def test_rule4_no_violation():
    z = [(1 if i % 2 else -1) * 0.5 for i in range(13)]
    assert hits(4, z) == []
    broken = [(1 if i % 2 else -1) * 0.5 for i in range(14)]
    broken[7] = broken[6]  # 한 번 끊김
    assert hits(4, broken) == []


# ---- Rule 5
def test_rule5_violation_two_of_three_same_side():
    assert hits(5, [0, 2.5, 0.1, 2.2]) == [(3, [1, 3])]
    assert hits(5, [-2.1, -2.3, 0]) == [(1, [0, 1])]


def test_rule5_no_violation_opposite_sides_or_spread():
    assert hits(5, [2.5, -2.5, 0]) == []
    assert hits(5, [2.5, 0, 0, 2.5]) == []  # 3점 창을 벗어남


# ---- Rule 6
def test_rule6_violation_four_of_five():
    assert hits(6, [1.2, 1.5, 0.2, 1.1, 1.3])[0][1] == [0, 1, 3, 4]


def test_rule6_no_violation():
    assert hits(6, [1.2, 1.5, 0.2, 0.3, 1.3]) == []
    assert hits(6, [1.2, -1.5, 1.2, -1.1, 1.3]) == []


# ---- Rule 7
def test_rule7_violation_fifteen_within_one_sigma():
    z = [0.5, -0.5] * 7 + [0.2]
    assert len(z) == 15 and hits(7, z) != []


def test_rule7_no_violation():
    assert hits(7, [0.5] * 14) == []
    z = [0.5] * 7 + [1.5] + [0.5] * 7
    assert hits(7, z) == []


# ---- Rule 8
def test_rule8_violation_eight_outside_both_sides():
    z = [1.5, -1.5] * 4
    assert hits(8, z)[0] == (7, list(range(8)))


def test_rule8_no_violation():
    assert hits(8, [1.5, -1.5] * 3 + [0.5, 1.5]) == []
    assert hits(8, [1.5] * 8) == []  # 한쪽에만 몰림 → 룰 8 아님


# ---- 설정 / 공통
def test_disabled_rule_not_evaluated():
    cfg = merge_config(NELSON_RULES, {"1": {"enabled": False}})
    assert all(v.rule_no != 1 for v in evaluate([5.0], [5.0], cfg))


def test_merge_config_overrides_params():
    cfg = merge_config(NELSON_RULES, {"2": {"params": {"count": 7}}})
    assert cfg[2]["params"]["count"] == 7 and cfg[3]["params"]["count"] == 6


@pytest.mark.parametrize(
    "config,expect",
    [
        ({"9": {"enabled": True}}, "1~8"),
        ({"2": {"params": {"count": 1}}}, "범위"),
        ({"2": {"params": {"count": 7.5}}}, "정수"),
        ({"5": {"params": {"window": 3, "min_points": 4}}}, "min_points"),
        ({"1": {"params": {"foo": 1}}}, "알 수 없는"),
    ],
)
def test_validate_config_errors(config, expect):
    errors = validate_config(config)
    assert errors and expect in errors[0]


def test_validate_config_ok():
    assert validate_config(NELSON_RULES) == []


def test_group_hits_separate_events():
    groups = group_hits(1, [(1, [1]), (5, [5])])
    assert [(g.first_index, g.last_index) for g in groups] == [(1, 1), (5, 5)]


def test_describe_korean():
    assert describe(5, P[5]) == "연속 3점 중 2점이 같은 쪽 2σ 밖"
