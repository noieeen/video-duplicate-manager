"""Group videos whose sampled frame hashes match (vectorised pairwise check over a duration-sorted sweep, union-find)."""
from __future__ import annotations

import numpy as np


def find_groups(
    items: list[tuple[int, float, dict[int, int]]],
    max_distance: int = 10,
    min_frames: int = 2,
    duration_tol: float = 1.0,
) -> list[list[int]]:
    """items: (file_id, duration, {frame_idx: hash}). Returns groups of file_ids (size >= 2).

    Two items match when their durations are within the window and at least `min_frames`
    shared frame indices have hamming distance <= max_distance.
    """
    if min_frames < 1:
        raise ValueError("min_frames must be >= 1")
    order = sorted(items, key=lambda it: it[1])
    n = len(order)
    durations = [it[1] for it in order]
    k_count = max((k for _, _, h in order for k in h), default=-1) + 1
    hashes = np.zeros((n, k_count), dtype=np.uint64)
    present = np.zeros((n, k_count), dtype=bool)
    for r, (_, _, h) in enumerate(order):
        for k, v in h.items():
            hashes[r, k] = v
            present[r, k] = True

    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    hi = 0
    for i in range(n):
        di = durations[i]
        hi = max(hi, i + 1)
        # Duration window: j is in-window while dj - di <= max(tol, 0.01 * dj). That predicate is
        # monotone in dj and only loosens as i grows, so `hi` never moves backwards.
        while hi < n and durations[hi] - di <= max(duration_tol, 0.01 * durations[hi]):
            hi += 1
        if hi == i + 1:
            continue
        close = np.bitwise_count(hashes[i] ^ hashes[i + 1 : hi]).astype(np.int32) <= max_distance
        close &= present[i] & present[i + 1 : hi]
        for off in np.nonzero(close.sum(axis=1) >= min_frames)[0]:
            ri, rj = find(i), find(i + 1 + int(off))
            if ri != rj:
                parent[rj] = ri

    components: dict[int, list[int]] = {}
    for i in range(n):
        components.setdefault(find(i), []).append(order[i][0])
    groups = [sorted(members) for members in components.values() if len(members) >= 2]
    groups.sort(key=lambda g: g[0])
    return groups
