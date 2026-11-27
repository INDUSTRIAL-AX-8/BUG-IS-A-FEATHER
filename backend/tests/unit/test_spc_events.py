"""SPC 위반 → 사건 묶기 단위 테스트."""

from datetime import datetime, timedelta

from app.core.spc.events import ViolationInfo, group_events, violation_direction

T0 = datetime(2026, 3, 4, 8, 0)
GAP = 3


def v(id_, rule, pts, direction, signal=None, char=1, acked=False, lots=None):
    signal = max(pts) if signal is None else signal
    return ViolationInfo(
        id=id_, characteristic_id=char, rule_no=rule, point_indices=list(pts), signal_index=signal,
        direction=direction, occurred_at=T0 + timedelta(hours=signal), acknowledged=acked,
        lot_ids=lots or [100 + i for i in pts],
    )


# ---------- 방향
def test_direction_of_level_rules_follows_side_of_center():
    assert violation_direction(1, [3.5], [60.0]) == 1
    assert violation_direction(2, [-0.5] * 9, [50.0] * 9) == -1
    assert violation_direction(5, [2.5, 0.1, 2.2], [1, 2, 3]) == 1
    assert violation_direction(6, [-1.2, -1.5, 0.3, -1.1, -1.4], [1, 2, 3, 4, 5]) == -1


def test_direction_of_trend_rule_follows_values():
    # z 는 모두 중심선 아래여도 값이 오르고 있으면 상승
    assert violation_direction(3, [-2.0, -1.5, -1.0, -0.5, -0.2, -0.1], [1, 2, 3, 4, 5, 6]) == 1
    assert violation_direction(3, [0.1] * 6, [6, 5, 4, 3, 2, 1]) == -1


def test_two_sided_rules_have_no_direction():
    for rule in (4, 7, 8):
        assert violation_direction(rule, [2.0, -2.0, 2.0], [1, 2, 3]) == 0


def test_direction_is_unknown_without_z():
    assert violation_direction(1, None, [60.0]) == 0


# ---------- 묶기
def test_rules_on_the_same_rise_form_one_event_labelled_with_every_rule():
    rows = [v(9, 1, [11], 1), v(13, 5, [10, 11], 1), v(11, 3, range(7, 13), 1), v(15, 6, range(8, 13), 1)]
    [ev] = group_events(rows, GAP)
    assert ev.rule_nos == [1, 3, 5, 6]
    assert ev.violation_ids == [9, 11, 13, 15]
    assert ev.id == 9
    assert ev.direction == 1
    assert ev.occurred_at == T0 + timedelta(hours=11)  # 가장 이른 신호 시각


def test_opposite_direction_starts_a_new_event_even_when_points_touch():
    rows = [v(1, 1, [0], -1), v(2, 2, range(0, 9), -1), v(3, 1, [10], 1)]
    events = group_events(rows, GAP)
    assert [e.violation_ids for e in events] == [[1, 2], [3]]
    assert [e.direction for e in events] == [-1, 1]


def test_long_window_rule_joins_the_same_direction_event_it_overlaps():
    # R2(9점 연속) 는 신호가 늦게 나오지만 앞 사건과 점이 겹치므로 같은 사건
    rows = [v(1, 1, [0], -1), v(2, 6, range(0, 4), -1), v(3, 2, range(0, 9), -1)]
    [ev] = group_events(rows, GAP)
    assert ev.rule_nos == [1, 2, 6]


def test_same_direction_far_apart_are_separate_events():
    rows = [v(1, 1, [2], 1), v(2, 1, [20], 1)]
    assert len(group_events(rows, GAP)) == 2


def test_two_sided_rule_joins_only_when_its_signal_is_near():
    near = [v(1, 1, [10], 1), v(2, 8, range(5, 13), 0)]
    assert len(group_events(near, GAP)) == 1
    # 창은 앞 사건과 겹치지만 신호가 멀면 별개 (긴 창이 사건 사이를 잇지 않게)
    far = [v(1, 1, [10], 1), v(2, 8, range(10, 18), 0)]
    assert len(group_events(far, GAP)) == 2


def test_mold_temperature_example_splits_into_three_events():
    # 사출기 3호 금형 온도: 오전 낮음(R1 R5 R6 R2) → 상승(R1 R5 R3 R6) → 정지 뒤 하락 구간(R8)
    rows = [
        v(8, 1, [0], -1), v(12, 5, [0, 1], -1), v(14, 6, range(0, 4), -1), v(10, 2, range(0, 9), -1),
        v(9, 1, [11], 1), v(13, 5, [10, 11], 1), v(11, 3, range(7, 13), 1), v(15, 6, range(8, 13), 1),
        v(16, 8, range(10, 18), 0),
    ]
    events = group_events(rows, GAP)
    assert [e.rule_nos for e in events] == [[1, 2, 5, 6], [1, 3, 5, 6], [8]]


def test_characteristics_are_grouped_separately():
    rows = [v(1, 1, [3], 1, char=1), v(2, 1, [3], 1, char=2)]
    events = group_events(rows, GAP)
    assert sorted(e.characteristic_id for e in events) == [1, 2]


def test_event_is_acknowledged_only_when_every_violation_is():
    [all_acked] = group_events([v(1, 1, [3], 1, acked=True), v(2, 5, [2, 3], 1, acked=True)], GAP)
    assert all_acked.acknowledged and not all_acked.partially_acknowledged
    # 확인한 뒤 새 룰이 더 걸리면 다시 미확인으로
    [partial] = group_events([v(1, 1, [3], 1, acked=True), v(2, 3, range(0, 6), 1)], GAP)
    assert not partial.acknowledged and partial.partially_acknowledged
    assert partial.unacknowledged_ids == [2]


def test_event_lots_are_the_union_of_its_violations():
    [ev] = group_events([v(1, 1, [3], 1, lots=[7, 8]), v(2, 5, [2, 3], 1, lots=[8, 9])], GAP)
    assert ev.lot_ids == [7, 8, 9]
    assert (ev.first_index, ev.last_index) == (2, 3)


def test_input_order_does_not_matter():
    rows = [v(3, 1, [10], 1), v(1, 1, [0], -1), v(2, 2, range(0, 9), -1)]
    assert [e.violation_ids for e in group_events(rows, GAP)] == [[1, 2], [3]]
