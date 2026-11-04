from engine.scoring import pick_keeper, score


def _m(path, score_, size):
    return {"path": path, "score": score_, "size": size}


def test_score_formula():
    assert score({"width": 320, "height": 240, "bit_rate": 1000}) == 320 * 240 * 1000


def test_score_zero_bitrate_uses_one():
    assert score({"width": 320, "height": 240, "bit_rate": 0}) == 320 * 240


def test_pick_keeper_highest_score_wins_and_returns_input_index():
    members = [_m("/a.mp4", 1.0, 100), _m("/b.mp4", 5.0, 10), _m("/c.mp4", 3.0, 999)]
    assert pick_keeper(members) == 1


def test_pick_keeper_tie_larger_size_wins():
    members = [_m("/a.mp4", 5.0, 100), _m("/b.mp4", 5.0, 200)]
    assert pick_keeper(members) == 1


def test_pick_keeper_tie_shorter_path_wins():
    members = [_m("/long/name.mp4", 5.0, 100), _m("/x.mp4", 5.0, 100)]
    assert pick_keeper(members) == 1


def test_pick_keeper_tie_path_ascending():
    members = [_m("/b.mp4", 5.0, 100), _m("/a.mp4", 5.0, 100)]
    assert pick_keeper(members) == 1
