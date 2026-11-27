"""계보 DAG 순수 함수: 깊이 제한 BFS, 순환 감지.

DB 에 의존하지 않도록 이웃 조회를 콜백으로 받는다. 콜백은 노드 집합을 한 번에 받아
{노드: [이웃...]} 을 돌려준다 (단계별 IN 배치 조회로 N+1 을 피함).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Hashable, Iterable
from typing import TypeVar

N = TypeVar("N", bound=Hashable)
NeighborFn = Callable[[set[N]], dict[N, list[N]]]


def bfs_levels(
    starts: Iterable[N],
    neighbors: NeighborFn,
    max_depth: int,
    max_nodes: int | None = None,
) -> tuple[dict[N, int], bool]:
    """시작 노드들로부터 깊이 max_depth 까지 탐색.

    반환: ({노드: 깊이}, truncated). max_nodes 를 넘으면 그 단계에서 멈추고 truncated=True.
    """
    depth: dict[N, int] = {s: 0 for s in starts}
    frontier = set(depth)
    level = 0
    truncated = False
    while frontier and level < max_depth:
        level += 1
        found = neighbors(frontier)
        nxt: set[N] = set()
        for node in frontier:
            for nb in found.get(node, ()):
                if nb not in depth:
                    nxt.add(nb)
        if max_nodes is not None and len(depth) + len(nxt) > max_nodes:
            room = max_nodes - len(depth)
            nxt = set(sorted(nxt, key=repr)[: max(room, 0)])
            truncated = True
        for nb in nxt:
            depth[nb] = level
        frontier = nxt
        if truncated:
            break
    return depth, truncated


def would_create_cycle(parent: N, child: N, children: NeighborFn, max_depth: int = 10_000) -> bool:
    """parent → child 엣지를 추가하면 순환이 생기는가? (child 의 하위에 parent 가 있으면 순환)"""
    if parent == child:
        return True
    reached, _ = bfs_levels([child], children, max_depth)
    return parent in reached


def find_cycle(edges: Iterable[tuple[N, N]]) -> list[N] | None:
    """엣지 목록에서 순환 하나를 찾아 경로로 반환 (없으면 None). 반복 DFS 로 재귀 한도 문제 없음."""
    graph: dict[N, list[N]] = defaultdict(list)
    nodes: set[N] = set()
    for p, c in edges:
        graph[p].append(c)
        nodes.update((p, c))

    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[N, int] = {n: WHITE for n in nodes}
    parent_of: dict[N, N] = {}

    for root in sorted(nodes, key=repr):
        if color[root] != WHITE:
            continue
        stack: list[tuple[N, int]] = [(root, 0)]
        color[root] = GRAY
        while stack:
            node, idx = stack[-1]
            succ = graph.get(node, [])
            if idx < len(succ):
                stack[-1] = (node, idx + 1)
                nxt = succ[idx]
                if color[nxt] == WHITE:
                    color[nxt] = GRAY
                    parent_of[nxt] = node
                    stack.append((nxt, 0))
                elif color[nxt] == GRAY:
                    path = [nxt]
                    cur = node
                    while cur != nxt:
                        path.append(cur)
                        cur = parent_of[cur]
                    path.append(nxt)
                    path.reverse()
                    return path
            else:
                color[node] = BLACK
                stack.pop()
    return None
