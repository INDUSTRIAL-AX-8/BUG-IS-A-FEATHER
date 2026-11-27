from app.core.genealogy.dag import bfs_levels, find_cycle, would_create_cycle


def _fn(graph):
    return lambda ids: {n: graph.get(n, []) for n in ids}


# 병합/분할 DAG: a,b → m → x,y ; c → y
CHILDREN = {"a": ["m"], "b": ["m"], "m": ["x", "y"], "c": ["y"]}
PARENTS = {"m": ["a", "b"], "x": ["m"], "y": ["m", "c"]}


def test_bfs_upstream_merge():
    depth, truncated = bfs_levels(["y"], _fn(PARENTS), max_depth=5)
    assert depth == {"y": 0, "m": 1, "c": 1, "a": 2, "b": 2}
    assert not truncated


def test_bfs_depth_limit():
    depth, _ = bfs_levels(["y"], _fn(PARENTS), max_depth=1)
    assert set(depth) == {"y", "m", "c"}


def test_bfs_max_nodes_truncates():
    depth, truncated = bfs_levels(["a"], _fn(CHILDREN), max_depth=5, max_nodes=2)
    assert truncated and len(depth) == 2


def test_would_create_cycle():
    assert would_create_cycle("x", "a", _fn(CHILDREN))  # a → … → x 이므로 x → a 는 순환
    assert would_create_cycle("m", "m", _fn(CHILDREN))
    assert not would_create_cycle("c", "x", _fn(CHILDREN))


def test_find_cycle():
    assert find_cycle([(1, 2), (2, 3), (1, 3)]) is None
    path = find_cycle([(1, 2), (2, 3), (3, 1), (4, 1)])
    assert path is not None and path[0] == path[-1] and set(path) == {1, 2, 3}


def test_find_cycle_long_chain_no_recursion_error():
    edges = [(i, i + 1) for i in range(20000)]
    assert find_cycle(edges) is None
    assert find_cycle(edges + [(20000, 0)]) is not None
