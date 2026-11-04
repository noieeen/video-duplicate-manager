"""Tests for engine/convert.py and the `files` / `convert` CLI commands."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from engine import convert, tools
from engine.convert import ConvertError, convert_file, output_path, run_convert
from engine.probe import probe

REPO = Path(__file__).resolve().parents[1]


class Sink:
    """Collects emitted events as dicts."""

    def __init__(self):
        self.events = []

    def __call__(self, type, **data):
        self.events.append({"type": type, **data})

    def of(self, type):
        return [e for e in self.events if e["type"] == type]


def _make_ts(dest, duration=12):
    """MPEG-TS with an h264 video stream and an AAC audio stream."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [tools.ffmpeg_bin(), "-v", "error", "-y",
           "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10",
           "-f", "lavfi", "-i", "sine=frequency=440",
           "-t", str(duration), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast",
           "-c:a", "aac", "-shortest", str(dest)]
    subprocess.run(cmd, check=True)
    return str(dest)


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _stream_codecs(path, select):
    r = subprocess.run(
        [tools.ffprobe_bin(), "-v", "error", "-select_streams", select,
         "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(path)],
        capture_output=True, check=True)
    return [line for line in r.stdout.decode().splitlines() if line.strip()]


def test_copy_mode_remuxes_ts_with_audio(tmp_path):
    src = _make_ts(tmp_path / "clip.ts")
    before = _sha(src)
    dst = output_path(src)
    assert dst == os.path.join(str(tmp_path), "clip.mp4")

    res = convert_file(src, dst, mode="copy")

    assert res["encoder"] == "copy"
    assert res["output"] == dst
    assert probe(dst)["codec"] == "h264"
    assert _stream_codecs(dst, "a"), "output has no audio stream"
    assert abs(probe(dst)["duration"] - probe(src)["duration"]) <= 1.0
    assert os.path.isfile(src) and _sha(src) == before
    assert not os.path.exists(dst + ".partial")


def test_output_path_avoids_existing_files(tmp_path):
    src = tmp_path / "x.ts"
    src.write_bytes(b"")
    assert output_path(str(src)) == str(tmp_path / "x.mp4")

    (tmp_path / "x.mp4").write_bytes(b"")
    assert output_path(str(src)) == str(tmp_path / "x_1.mp4")

    (tmp_path / "x_1.mp4").write_bytes(b"")
    assert output_path(str(src)) == str(tmp_path / "x_2.mp4")

    # A source that is itself the .mp4 target is never returned.
    mp4 = tmp_path / "y.mp4"
    mp4.write_bytes(b"")
    assert output_path(str(mp4)) == str(tmp_path / "y_1.mp4")


def test_output_path_out_dir(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    src = tmp_path / "x.ts"
    src.write_bytes(b"")
    assert output_path(str(src), str(out)) == str(out / "x.mp4")
    (out / "x.mp4").write_bytes(b"")
    assert output_path(str(src), str(out)) == str(out / "x_1.mp4")


def test_failure_leaves_no_output(tmp_path):
    bad = tmp_path / "bad.ts"
    bad.write_text("this is not a video")
    dst = output_path(str(bad))

    with pytest.raises(ConvertError):
        convert_file(str(bad), dst, mode="copy")

    assert not os.path.exists(dst)
    assert not os.path.exists(dst + ".partial")
    assert bad.exists()


def test_reencode_x264(tmp_path):
    src = _make_ts(tmp_path / "short.ts", duration=5)
    dst = output_path(src)

    res = convert_file(src, dst, mode="reencode", encoder="x264")

    assert res["encoder"] == "x264"
    assert probe(dst)["codec"] == "h264"
    assert _stream_codecs(dst, "a")
    assert abs(probe(dst)["duration"] - probe(src)["duration"]) <= 1.0
    assert not os.path.exists(dst + ".partial")


@pytest.mark.skipif(not convert.nvenc_available(), reason="NVIDIA h264_nvenc not available")
def test_reencode_nvenc(tmp_path):
    src = _make_ts(tmp_path / "short.ts", duration=5)
    dst = output_path(src)

    res = convert_file(src, dst, mode="reencode", encoder="nvenc")

    assert res["encoder"] == "nvenc"
    assert probe(dst)["codec"] == "h264"
    assert abs(probe(dst)["duration"] - probe(src)["duration"]) <= 1.0
    assert not os.path.exists(dst + ".partial")


def test_on_percent_is_monotonic_and_ends_at_100(tmp_path):
    src = _make_ts(tmp_path / "clip.ts", duration=12)
    seen = []

    convert_file(src, output_path(src), mode="reencode", encoder="x264", on_percent=seen.append)

    assert seen, "no progress reported"
    assert seen[0] == 0
    assert seen == sorted(seen)
    assert seen[-1] == 100
    assert seen.count(100) == 1
    assert all(isinstance(p, int) and 0 <= p <= 100 for p in seen)


def test_run_convert_skips_and_converts(tmp_path, make_video):
    ts = _make_ts(tmp_path / "a.ts", duration=12)
    mp4 = make_video(tmp_path / "b.mp4", duration=5)
    bad = tmp_path / "bad.ts"
    bad.write_text("not video")
    missing = str(tmp_path / "nope.ts")
    sink = Sink()

    summary = run_convert(None, [ts, mp4, missing, str(bad)], sink, mode="copy")

    assert sink.events[0]["type"] == "convert_started"
    assert sink.events[0]["total"] == 4
    skips = {e["path"]: e["reason"] for e in sink.of("skip")}
    assert skips == {mp4: "already mp4", missing: "file not found"}
    assert [e["path"] for e in sink.of("file_error")] == [str(bad)]
    converted = sink.of("converted")
    assert len(converted) == 1
    assert converted[0]["output"] == os.path.join(str(tmp_path), "a.mp4")
    assert os.path.isfile(converted[0]["output"])

    done = sink.of("convert_done")[0]
    assert done["converted"] == 1
    assert done["failed"] == 1
    assert done["skipped"] == 2
    assert done["outputs"] == [converted[0]["output"]]
    assert summary == {k: done[k] for k in ("converted", "failed", "skipped", "outputs")}


def _run(db, *args):
    proc = subprocess.run(
        [sys.executable, "-m", "engine", "--db", str(db), *args],
        cwd=REPO, capture_output=True, timeout=600,
    )
    events = [json.loads(line) for line in proc.stdout.decode("utf-8").splitlines() if line.strip()]
    return proc.returncode, events


def _of(events, type):
    return [e for e in events if e["type"] == type]


def test_cli_files_and_convert_from_scan(tmp_path, make_video):
    root = tmp_path / "root"
    ts = _make_ts(root / "show" / "episode.ts", duration=12)
    make_video(root / "movie.mp4", duration=5)
    db = tmp_path / "lib" / "library.db"

    code, _events = _run(db, "scan", "--root", str(root))
    assert code == 0

    code, events = _run(db, "files", "--ext", ".ts")
    assert code == 0
    paths = _of(events, "files")[0]["paths"]
    assert [os.path.normcase(p) for p in paths] == [os.path.normcase(os.path.abspath(ts))]

    code, events = _run(db, "convert", "--from-scan-ext", ".ts")
    assert code == 0
    assert _of(events, "converted")
    done = _of(events, "convert_done")[0]
    assert done["converted"] == 1 and done["failed"] == 0 and done["skipped"] == 0
    assert os.path.isfile(os.path.join(os.path.dirname(ts), "episode.mp4"))


def test_cli_convert_without_inputs_fails(tmp_path):
    code, events = _run(tmp_path / "lib" / "library.db", "convert")
    assert code == 1
    errors = _of(events, "error")
    assert len(errors) == 1
    assert errors[0]["message"]
