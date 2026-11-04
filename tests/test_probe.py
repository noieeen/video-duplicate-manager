import json
import subprocess

import pytest

from engine import tools
from engine.probe import ProbeError, _parse_rate, probe


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


def test_probe_fps_and_hdr_for_ordinary_clip(tmp_path, make_video):
    p = make_video(tmp_path / "c.mp4", source="testsrc", w=320, h=240, duration=5)
    m = probe(p)
    assert m["fps"] == pytest.approx(2.0, abs=0.01)  # make_video renders at 2 fps
    assert m["hdr"] is False


def test_probe_hdr_from_transfer_characteristics(tmp_path):
    dest = tmp_path / "hdr.mp4"
    subprocess.run([tools.ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=2",
                    "-t", "2", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p10le",
                    "-x264-params", "colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc",
                    str(dest)], check=True)
    assert probe(str(dest))["hdr"] is True


@pytest.mark.parametrize("value,expected", [
    ("30000/1001", 30000 / 1001),
    ("25/1", 25.0),
    ("30", 30.0),
    ("0/0", None),
    ("10/0", None),
    ("N/A", None),
    ("-5/1", None),
    ("", None),
    (None, None),
])
def test_parse_rate(value, expected):
    got = _parse_rate(value)
    if expected is None:
        assert got is None
    else:
        assert got == pytest.approx(expected)


def _fake_ffprobe(stream_extra, monkeypatch):
    stream = {"width": 320, "height": 240, "codec_name": "h264", **stream_extra}
    data = {"streams": [stream], "format": {"duration": "3.0"}}

    def fake_run(cmd, timeout=60):
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(data).encode(), stderr=b"")

    monkeypatch.setattr(tools, "run", fake_run)


def test_probe_fps_falls_back_to_r_frame_rate(monkeypatch):
    _fake_ffprobe({"avg_frame_rate": "0/0", "r_frame_rate": "25/1"}, monkeypatch)
    assert probe("x.mp4")["fps"] == 25.0


def test_probe_fps_none_when_rates_missing(monkeypatch):
    _fake_ffprobe({}, monkeypatch)
    assert probe("x.mp4")["fps"] is None
