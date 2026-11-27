"""SPC 위반 → 사건 묶기 — 순수 함수.

같은 이상 하나에 넬슨룰 여러 개가 동시에 성립하면 룰마다 위반이 하나씩 생긴다. 이를 사건 1건으로 묶고,
걸린 룰 번호는 사건의 라벨로 남긴다 (경보 피로·원인 점수 중복 계산 방지). 위반 자체는 그대로 저장된다.

묶는 규칙 (특성별, 신호 점 순서대로 직전 사건과 비교)
  - 방향: R1·R2·R5·R6 은 포함 점 z 합의 부호, R3 은 값의 추세 방향, R4·R7·R8 은 양쪽(0)
  - 방향 있는 위반: 방향이 맞고(같거나 한쪽이 0) 포함 점이 직전 사건 마지막 점 + gap 이내에서 시작하면 붙인다
    (창이 긴 R2 가 겹치는 같은 방향 사건에 붙도록)
  - 양쪽 위반: 신호 점이 직전 사건 마지막 점 + gap 이내일 때만 붙인다 (긴 창이 사건 사이를 잇지 않게)
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

LEVEL_RULES = (1, 2, 5, 6)
TREND_RULE = 3


def _sign(x: float) -> int:
    return 1 if x > 0 else -1 if x < 0 else 0


def violation_direction(rule_no: int, z: Sequence[float] | None, values: Sequence[float] | None) -> int:
    """+1 위로(상승), -1 아래로(하락), 0 양쪽 또는 알 수 없음."""
    if rule_no in LEVEL_RULES:
        return _sign(sum(z)) if z else 0
    if rule_no == TREND_RULE:
        return _sign(values[-1] - values[0]) if values and len(values) > 1 else 0
    return 0


@dataclass
class ViolationInfo:
    id: int | None
    characteristic_id: int
    rule_no: int
    point_indices: list[int]  # 관리도 점 인덱스
    signal_index: int  # 룰이 처음 성립한 점
    direction: int
    occurred_at: datetime
    acknowledged: bool = False
    lot_ids: list[int] = field(default_factory=list)


@dataclass
class SpcEvent:
    characteristic_id: int
    direction: int
    members: list[ViolationInfo] = field(default_factory=list)

    @property
    def violation_ids(self) -> list[int]:
        return sorted(m.id for m in self.members if m.id is not None)

    @property
    def id(self) -> int | None:
        ids = self.violation_ids
        return ids[0] if ids else None

    @property
    def rule_nos(self) -> list[int]:
        return sorted({m.rule_no for m in self.members})

    @property
    def first_index(self) -> int:
        return min(i for m in self.members for i in m.point_indices)

    @property
    def last_index(self) -> int:
        return max(max(m.point_indices, default=m.signal_index) for m in self.members)

    @property
    def signal_index(self) -> int:
        return min(m.signal_index for m in self.members)

    @property
    def occurred_at(self) -> datetime:
        return min(m.occurred_at for m in self.members)

    @property
    def lot_ids(self) -> list[int]:
        return sorted({lid for m in self.members for lid in m.lot_ids})

    @property
    def acknowledged(self) -> bool:
        return all(m.acknowledged for m in self.members)

    @property
    def partially_acknowledged(self) -> bool:
        return not self.acknowledged and any(m.acknowledged for m in self.members)

    @property
    def unacknowledged_ids(self) -> list[int]:
        return sorted(m.id for m in self.members if not m.acknowledged and m.id is not None)

    def accepts(self, v: ViolationInfo, gap: int) -> bool:
        reach = self.last_index + gap
        if v.direction == 0:
            return v.signal_index <= reach
        if self.direction not in (0, v.direction):
            return False
        return min(v.point_indices, default=v.signal_index) <= reach

    def add(self, v: ViolationInfo) -> None:
        self.members.append(v)
        if self.direction == 0:
            self.direction = v.direction


def group_events(violations: Iterable[ViolationInfo], gap: int) -> list[SpcEvent]:
    by_char: dict[int, list[ViolationInfo]] = defaultdict(list)
    for v in violations:
        by_char[v.characteristic_id].append(v)
    events: list[SpcEvent] = []
    for char_id, rows in by_char.items():
        current: SpcEvent | None = None
        for v in sorted(rows, key=lambda r: (r.signal_index, r.occurred_at, r.rule_no)):
            if current is None or not current.accepts(v, gap):
                current = SpcEvent(char_id, v.direction)
                events.append(current)
            current.add(v)
    return events
