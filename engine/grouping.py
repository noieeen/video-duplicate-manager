"""Group videos whose sampled frame hashes match (union-find over a duration-sorted sweep)."""
from __future__ import annotations


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def _matches(
    a: tuple[int, float, dict[int, int]],
    b: tuple[int, float, dict[int, int]],
    max_distance: int,
    min_frames: int,
    duration_tol: float,
) -> bool:
    _, da, ha = a
    _, db, hb = b
    if abs(da - db) > max(duration_tol, 0.01 * max(da, db)):
        return False
    shared = ha.keys() & hb.keys()
    if len(shared) < min_frames:
        return False
    return all(_hamming(ha[i], hb[i]) <= max_distance for i in shared)


def find_groups(
    items: list[tuple[int, float, dict[int, int]]],
    max_distance: int = 10,
    min_frames: int = 2,
    duration_tol: float = 1.0,
) -> list[list[int]]:
    """items: (file_id, duration, {frame_idx: hash}). Returns groups of file_ids (size >= 2)."""
    order = sorted(items, key=lambda it: it[1])
    n = len(order)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(n):
        di = order[i][1]
        for j in range(i + 1, n):
            dj = order[j][1]
            # Sorted by duration, so once the window is exceeded no later j can match.
            if dj - di > max(duration_tol, 0.01 * dj):
                break
            ri, rj = find(i), find(j)
            if ri == rj:
                continue
            if _matches(order[i], order[j], max_distance, min_frames, duration_tol):
                parent[rj] = ri

    components: dict[int, list[int]] = {}
    for i in range(n):
        components.setdefault(find(i), []).append(order[i][0])
    groups = [sorted(members) for members in components.values() if len(members) >= 2]
    groups.sort(key=lambda g: g[0])
    return groups
