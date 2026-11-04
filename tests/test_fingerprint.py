from engine.fingerprint import extract_frame, fingerprint, hamming, sample_times


def _hashes(fp):
    return [h for _, h, _ in fp]


def test_sample_times_fractions():
    assert sample_times(100) == [10, 50, 90]


def test_same_video_reencoded_at_other_resolution_matches(tmp_path, make_video):
    a = make_video(tmp_path / "small.mp4", source="testsrc", w=320, h=240, duration=30)
    b = make_video(tmp_path / "big.mp4", source="testsrc", w=640, h=480, duration=30)
    ha = _hashes(fingerprint(a, 30))
    hb = _hashes(fingerprint(b, 30))
    assert all(h is not None for h in ha)
    assert all(h is not None for h in hb)
    for x, y in zip(ha, hb):
        assert hamming(x, y) <= 10


def test_different_content_has_a_distant_frame(tmp_path, make_video):
    a = make_video(tmp_path / "test.mp4", source="testsrc", duration=30)
    b = make_video(tmp_path / "mandel.mp4", source="mandelbrot", duration=30)
    ha = _hashes(fingerprint(a, 30))
    hb = _hashes(fingerprint(b, 30))
    pairs = [(x, y) for x, y in zip(ha, hb) if x is not None and y is not None]
    assert pairs
    assert any(hamming(x, y) > 10 for x, y in pairs)


def test_black_video_has_no_hashes(tmp_path, make_video):
    p = make_video(tmp_path / "black.mp4", source="black", duration=30)
    fp = fingerprint(p, 30)
    assert _hashes(fp) == [None, None, None]
    assert all(img is None for _, _, img in fp)


def test_short_20_second_video_is_fingerprinted(tmp_path, make_video):
    p = make_video(tmp_path / "short.mp4", source="testsrc", duration=20)
    assert all(h is not None for h in _hashes(fingerprint(p, 20)))


def test_extract_frame_missing_file_returns_none(tmp_path):
    assert extract_frame(str(tmp_path / "nope.mp4"), 1.0) is None
