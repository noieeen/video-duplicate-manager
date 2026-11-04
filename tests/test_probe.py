import pytest

from engine.probe import ProbeError, probe


def test_probe_mp4(tmp_path, make_video):
    p = make_video(tmp_path / "a.mp4", source="testsrc", w=320, h=240, duration=10)
    m = probe(p)
    assert m["width"] == 320
    assert m["height"] == 240
    assert abs(m["duration"] - 10) <= 0.5
    assert m["bit_rate"] > 0
    assert m["codec"] == "h264"


def test_probe_ts_uses_bitrate_fallback(tmp_path, make_video):
    p = make_video(tmp_path / "b.ts", source="testsrc", w=320, h=240, duration=10)
    m = probe(p)
    assert m["bit_rate"] > 0
    assert m["width"] == 320
    assert m["height"] == 240


def test_probe_non_video_raises(tmp_path):
    p = tmp_path / "notes.txt"
    p.write_text("this is not a video")
    with pytest.raises(ProbeError):
        probe(str(p))
