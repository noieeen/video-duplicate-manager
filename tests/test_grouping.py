import random
import time

import pytest

from engine.grouping import find_groups

FULL = {0: 0, 1: 0, 2: 0}
FAR = (1 << 11) - 1  # hamming 11 from 0


def _reference(items, max_distance, min_frames, duration_tol):
    """Brute force: check every pair (i<j) with the min_frames rule, then union matches."""
    order = sorted(items, key=lambda it: it[1])
    n = len(order)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            x = parent[x]
        return x

    for i in range(n):
        _, di, hi_ = order[i]
        for j in range(i + 1, n):
            _, dj, hj = order[j]
            if abs(di - dj) > max(duration_tol, 0.01 * max(di, dj)):
                continue
            close = sum(
                1 for k in hi_.keys() & hj.keys()
                if bin(hi_[k] ^ hj[k]).count("1") <= max_distance
            )
            if close >= min_frames:
                parent[find(j)] = find(i)

    components = {}
    for i in range(n):
        components.setdefault(find(i), []).append(order[i][0])
    groups = [sorted(m) for m in components.values() if len(m) >= 2]
    groups.sort(key=lambda g: g[0])
    return groups


def test_identical_hashes_same_duration_one_group():
    items = [(1, 60.0, dict(FULL)), (2, 60.0, dict(FULL)), (3, 500.0, {0: 1 << 40})]
    assert find_groups(items) == [[1, 2]]


def test_same_hashes_different_duration_no_group():
    items = [(1, 100.0, dict(FULL)), (2, 130.0, dict(FULL))]
    assert find_groups(items) == []


def test_only_one_shared_frame_below_min_frames_no_group():
    items = [(1, 60.0, {0: 0, 1: 0}), (2, 60.0, {0: 0})]
    assert find_groups(items, min_frames=2) == []


def test_two_of_three_frames_close_one_far_groups():
    items = [(1, 60.0, {0: 0, 1: 0, 2: 0}), (2, 60.0, {0: 0, 1: 0, 2: FAR})]
    assert find_groups(items, max_distance=10, min_frames=2) == [[1, 2]]


def test_only_one_of_three_frames_close_no_group():
    items = [(1, 60.0, {0: 0, 1: 0, 2: 0}), (2, 60.0, {0: 0, 1: FAR, 2: FAR})]
    assert find_groups(items, max_distance=10, min_frames=2) == []


def test_two_close_one_far_with_min_frames_3_no_group():
    items = [(1, 60.0, {0: 0, 1: 0, 2: 0}), (2, 60.0, {0: 0, 1: 0, 2: FAR})]
    assert find_groups(items, max_distance=10, min_frames=3) == []


def test_transitive_match_forms_one_group():
    # A~B (shared 0,1), B~C (shared 1,2), A and C share only index 1 -> no direct match.
    a = (1, 60.0, {0: 0, 1: 0})
    b = (2, 60.0, {0: 0, 1: 0, 2: 0})
    c = (3, 60.0, {1: 0, 2: 0})
    assert find_groups([c, a, b]) == [[1, 2, 3]]


def test_singletons_are_dropped_and_groups_sorted_by_first_member():
    items = [
        (9, 60.0, dict(FULL)), (4, 60.0, dict(FULL)),
        (7, 300.0, {0: 0, 1: 0}), (2, 300.0, {0: 0, 1: 0}),
        (5, 900.0, {0: 1 << 40, 1: 1 << 41}),
    ]
    assert find_groups(items) == [[2, 7], [4, 9]]


def test_min_frames_below_one_raises():
    with pytest.raises(ValueError):
        find_groups([(1, 60.0, dict(FULL))], min_frames=0)


def test_matches_bruteforce_reference():
    for seed in range(5):
        rng = random.Random(seed)
        bases = [rng.getrandbits(64) for _ in range(4)]
        durations = [60.0, 60.0, 60.0, 1440.0, 1440.5, 1441.0, 2000.0, 30.0]
        n_frames = rng.choice([2, 3, 5])
        items = []
        for fid in range(60):
            frames = {}
            for k in range(n_frames):
                if rng.random() < 0.2:
                    continue  # missing frame
                h = rng.choice(bases)
                for bit in rng.sample(range(64), rng.randint(0, 14)):
                    h ^= 1 << bit
                frames[k] = h
            items.append((fid, rng.choice(durations), frames))
        for max_distance in (0, 10, 20):
            for min_frames in (1, 2, 3):
                expected = _reference(items, max_distance, min_frames, 1.0)
                got = find_groups(items, max_distance=max_distance, min_frames=min_frames)
                assert got == expected, (seed, max_distance, min_frames)


def test_5000_random_items_fast():
    rng = random.Random(0)
    items = [
        (i, rng.uniform(0, 100_000.0), {f: rng.getrandbits(64) for f in range(3)})
        for i in range(5000)
    ]
    start = time.perf_counter()
    groups = find_groups(items)
    elapsed = time.perf_counter() - start
    assert groups == []
    assert elapsed < 5.0


def test_5000_same_duration_fast():
    rng = random.Random(0)
    items = [
        (i, 1440.0, {f: rng.getrandbits(64) for f in range(3)})
        for i in range(5000)
    ]
    start = time.perf_counter()
    groups = find_groups(items)
    elapsed = time.perf_counter() - start
    assert groups == []
    assert elapsed < 5.0
