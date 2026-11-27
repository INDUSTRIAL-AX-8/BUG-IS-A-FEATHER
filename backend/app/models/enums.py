from __future__ import annotations

import enum


class ItemCategory(str, enum.Enum):
    RAW = "RAW"  # 원자재
    PART = "PART"  # 부품
    SEMI = "SEMI"  # 반제품
    FINISHED = "FINISHED"  # 완제품


class ChartType(str, enum.Enum):
    AUTO = "AUTO"
    IMR = "IMR"
    XBAR_R = "XBAR_R"
    XBAR_S = "XBAR_S"


class BaselineScope(str, enum.Enum):
    GLOBAL = "GLOBAL"  # 전체 데이터 하나의 관리한계 (기준 구간 고정 가능)
    PARENT_LOT = "PARENT_LOT"  # 상위 LOT(런·배치·용탕 등 체제)별 기준선 — KAMP 런별 기준선 방식


class LotStatus(str, enum.Enum):
    NORMAL = "NORMAL"  # 정상
    HOLD = "HOLD"  # 보류
    QUARANTINE = "QUARANTINE"  # 격리
    DEFECT = "DEFECT"  # 불량


class NodeType(str, enum.Enum):
    LOT = "LOT"
    EQUIPMENT = "EQUIPMENT"
    STEP = "STEP"
    CONDITION = "CONDITION"


class Role(str, enum.Enum):
    VIEWER = "VIEWER"  # 조회자
    OPERATOR = "OPERATOR"  # 실무자
    ADMIN = "ADMIN"  # 관리자

    @property
    def level(self) -> int:
        return {"VIEWER": 0, "OPERATOR": 1, "ADMIN": 2}[self.value]


class ForecastStatus(str, enum.Enum):
    OK = "OK"
    INSUFFICIENT = "INSUFFICIENT"  # 데이터 부족
    FAILED = "FAILED"  # 적합 실패


NODE_PREFIX: dict[NodeType, str] = {
    NodeType.LOT: "lot",
    NodeType.STEP: "step",
    NodeType.EQUIPMENT: "eq",
    NodeType.CONDITION: "cond",
}
PREFIX_NODE: dict[str, NodeType] = {v: k for k, v in NODE_PREFIX.items()}


def node_key(node_type: NodeType, node_id: int) -> str:
    return f"{NODE_PREFIX[node_type]}:{node_id}"


def parse_node_key(key: str) -> tuple[NodeType, int]:
    """'lot:12' → (NodeType.LOT, 12). 형식이 틀리면 ValueError."""
    prefix, _, raw_id = key.partition(":")
    if prefix not in PREFIX_NODE or not raw_id.isdigit():
        raise ValueError(f"노드 ID 형식이 올바르지 않습니다: {key!r} (예: lot:12, eq:3)")
    return PREFIX_NODE[prefix], int(raw_id)
