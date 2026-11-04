import random
import time

from engine.grouping import find_groups

FULL = {0: 0, 1: 0, 2: 0}


def test_identical_hashes_same_duration_one_group():
    items = [(1, 60.0, dict(FULL)), (2, 60.0, dict(FULL)), (3, 500.0, {0: 1 << 40})]
    assert find_groups(items) == [[1, 2]]


def test_same_hashes_different_duration_no_group():
    items = [(1, 100.0, dict(FULL)), (2, 130.0, dict(FULL))]
    assert find_groups(items) == []


def test_only_one_shared_frame_below_min_frames_no_group():
    items = [(1, 60.0, {0: 0, 1: 0}), (2, 60.0, {0: 0})]
    assert find_groups(items, min_frames=2) == []


def test_one_frame_over_max_distance_no_group():
    far = (1 << 11) - 1  # hamming 11 from 0
    items = [(1, 60.0, {0: 0, 1: 0, 2: 0}), (2, 60.0, {0: 0, 1: 0, 2: far})]
    assert find_groups(items, max_distance=10) == []


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
