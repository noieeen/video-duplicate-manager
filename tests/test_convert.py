"""Tests for engine/convert.py and the `files` / `convert` CLI commands."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from engine import convert, tools
from engine.convert import ConvertError, ConvertSkipped, convert_file, output_path, run_convert
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


def test_reencode_x264_fast_speed(tmp_path):
    src = _make_ts(tmp_path / "short.ts", duration=5)
    dst = output_path(src)

    res = convert_file(src, dst, mode="reencode", encoder="x264", speed="fast")

    assert res["speed"] == "fast"
    assert probe(dst)["codec"] == "h264"
    assert abs(probe(dst)["duration"] - probe(src)["duration"]) <= 1.0
    assert not os.path.exists(dst + ".partial")


def test_copy_mode_speed_is_none(tmp_path):
    src = _make_ts(tmp_path / "short.ts", duration=5)
    res = convert_file(src, output_path(src), mode="copy")
    assert res["speed"] is None


def test_unknown_speed_raises(tmp_path):
    src = _make_ts(tmp_path / "short.ts", duration=5)
    dst = output_path(src)
    with pytest.raises(ConvertError, match="unknown speed: bogus"):
        convert_file(src, dst, mode="reencode", encoder="x264", speed="bogus")
    with pytest.raises(ConvertError, match="unknown speed: bogus"):
        run_convert(None, [src], Sink(), mode="reencode", encoder="x264", speed="bogus")
    assert not os.path.exists(dst)


def test_speed_selects_preset_in_ffmpeg_command(tmp_path):
    # Command-builder only; no encode, so no GPU is needed.
    def preset(enc, speed):
        cmd = convert._ffmpeg_cmd("in.ts", "out.mp4.partial", "reencode", enc, speed)
        return cmd[cmd.index("-preset") + 1]

    assert preset("nvenc", "fast") == "p1"
    assert preset("nvenc", "quality") == "p5"
    assert preset("x264", "fast") == "veryfast"
    assert preset("x264", "quality") == "medium"


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


def _touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return str(path)


def _real(paths):
    return [os.path.realpath(p) for p in paths]


def test_video_files_under_default_skips_mp4_and_keeps_casing(tmp_path):
    root = tmp_path / "root"
    a = _touch(root / "a.ts")
    b = _touch(root / "sub" / "deep" / "b.TS")
    _touch(root / "c.mp4")
    d = _touch(root / "d.mkv")
    _touch(root / "notes.txt")

    got = convert.video_files_under([str(root)])
    assert got == sorted(_real([a, b, d]))
    assert _real([b])[0].endswith("b.TS")


def test_video_files_under_with_ext_and_dedupe(tmp_path):
    root = tmp_path / "root"
    a = _touch(root / "a.ts")
    b = _touch(root / "sub" / "b.TS")
    _touch(root / "d.mkv")

    assert convert.video_files_under([str(root)], ".ts") == sorted(_real([a, b]))
    assert convert.video_files_under([str(root)], "TS") == sorted(_real([a, b]))
    # the same folder given twice, and a folder nested in another, must not duplicate files
    assert convert.video_files_under([str(root), str(root / "sub")], ".ts") == sorted(_real([a, b]))


def test_video_files_under_missing_root_raises(tmp_path):
    with pytest.raises(ValueError):
        convert.video_files_under([str(tmp_path / "missing")])


def test_cli_files_root_lists_files_from_disk(tmp_path):
    root = tmp_path / "root"
    a = _touch(root / "a.ts")
    _touch(root / "c.mp4")
    db = tmp_path / "lib" / "library.db"

    code, events = _run(db, "files", "--root", str(root))
    assert code == 0
    assert _of(events, "files")[0]["paths"] == sorted(_real([a]))

    code, events = _run(db, "files", "--root", str(root), "--ext", ".mp4")
    assert code == 0
    assert _of(events, "files")[0]["paths"] == sorted(_real([str(root / "c.mp4")]))


def test_cli_files_root_missing_folder_is_error(tmp_path):
    db = tmp_path / "lib" / "library.db"
    code, events = _run(db, "files", "--root", str(tmp_path / "nope"))
    assert code == 1
    assert _of(events, "error")


def test_cli_files_without_root_or_ext_is_usage_error(tmp_path):
    db = tmp_path / "lib" / "library.db"
    code, events = _run(db, "files")
    assert code == 2
    assert not events


# --- shrink -----------------------------------------------------------------------------------

def _lists_encoder(name):
    r = subprocess.run([tools.ffmpeg_bin(), "-hide_banner", "-encoders"], capture_output=True)
    return name.encode() in r.stdout


@pytest.fixture(scope="session")
def _svtav1_listed():
    if not _lists_encoder("libsvtav1"):
        pytest.skip("ffmpeg has no libsvtav1")
    return True


@pytest.fixture
def shrink_env(monkeypatch, _svtav1_listed):
    """Shrink tests use the software AV1 encoder, so they need no GPU."""
    monkeypatch.setattr(convert, "_av1_encoder", lambda: "svtav1")
    return monkeypatch


def _make_clip(dest, w=320, h=240, rate=10, duration=1, vcodec="libx264", noisy=True):
    """Short test clip. `noisy` adds grain so the source is large enough for AV1 CRF 35 to be under 85% of it."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    lavfi = f"testsrc2=size={w}x{h}:rate={rate}"
    if noisy:
        lavfi += ",noise=alls=30:allf=t"
    cmd = [tools.ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", lavfi, "-t", str(duration),
           "-c:v", vcodec, "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    cmd += ["-crf", "18" if vcodec == "libx264" else "28"]
    subprocess.run(cmd + [str(dest)], check=True)
    return str(dest)


@pytest.mark.parametrize("w,h,expected", [
    (2560, 1440, (1920, 1080)),   # landscape: short side capped at 1080
    (1080, 1920, (1080, 1920)),   # portrait already at the cap: untouched
    (2160, 3840, (1080, 1920)),   # portrait: short side (width) becomes 1080
    (1280, 720, (1280, 720)),     # under the cap: never upscaled
])
def test_shrink_caps_short_side_at_1080(tmp_path, shrink_env, w, h, expected):
    src = _make_clip(tmp_path / "in.mp4", w=w, h=h, rate=5, duration=1)
    res = convert_file(src, output_path(src, suffix="_av1"), mode="shrink")
    out = probe(res["output"])
    assert (out["width"], out["height"]) == expected
    assert res["encoder"] == "svtav1"


@pytest.mark.parametrize("rate,expected", [(120, 60), (30, 30)])
def test_shrink_caps_fps_at_60(tmp_path, shrink_env, rate, expected):
    src = _make_clip(tmp_path / "in.mp4", w=640, h=480, rate=rate, duration=1)
    res = convert_file(src, output_path(src, suffix="_av1"), mode="shrink")
    assert probe(res["output"])["fps"] == pytest.approx(expected, abs=0.5)


def test_shrink_skips_hdr_source(tmp_path, shrink_env, monkeypatch):
    src = _make_clip(tmp_path / "hdr.mp4", w=320, h=240, rate=10, duration=1)
    real_probe = convert.probe
    monkeypatch.setattr(convert, "probe", lambda p: {**real_probe(p), "hdr": True})
    sink = Sink()

    summary = run_convert(None, [src], sink, mode="shrink")

    assert [(e["path"], e["reason"]) for e in sink.of("skip")] == [(src, "HDR source (Shrink keeps SDR only)")]
    assert not sink.of("file_error")
    assert summary["skipped"] == 1 and summary["failed"] == 0 and summary["converted"] == 0
    assert not os.path.exists(output_path(src, suffix="_av1"))


def test_shrink_skips_hevc_within_cap(tmp_path, shrink_env):
    if not _lists_encoder("libx265"):
        pytest.skip("ffmpeg has no libx265")
    src = _make_clip(tmp_path / "hevc.mp4", w=1920, h=1080, rate=5, duration=1, vcodec="libx265", noisy=False)
    assert probe(src)["codec"] == "hevc"
    sink = Sink()

    summary = run_convert(None, [src], sink, mode="shrink")

    assert [e["reason"] for e in sink.of("skip")] == ["already HEVC/AV1 within 1080p/60 fps"]
    assert summary["skipped"] == 1 and summary["converted"] == 0
    assert not os.path.exists(output_path(src, suffix="_av1"))


def test_shrink_keep_rule_discards_output(tmp_path, shrink_env, monkeypatch):
    src = _make_clip(tmp_path / "in.mp4", w=320, h=240, rate=10, duration=1)
    monkeypatch.setattr(convert, "KEEP_RATIO", 0.0)
    dst = output_path(src, suffix="_av1")

    with pytest.raises(ConvertSkipped, match="not smaller: output was \\d+% of the original"):
        convert_file(src, dst, mode="shrink")
    assert not os.path.exists(dst)
    assert not os.path.exists(dst + ".partial")

    sink = Sink()
    summary = run_convert(None, [src], sink, mode="shrink")
    reasons = [e["reason"] for e in sink.of("skip")]
    assert len(reasons) == 1 and reasons[0].startswith("not smaller: output was ")
    assert not sink.of("file_error")
    assert summary["skipped"] == 1 and summary["failed"] == 0
    assert not os.path.exists(dst) and not os.path.exists(dst + ".partial")


def test_shrink_accepts_mp4_input_and_names_output(tmp_path, shrink_env):
    src = _make_clip(tmp_path / "movie.mp4", w=640, h=480, rate=10, duration=1)
    sink = Sink()

    run_convert(None, [src], sink, mode="shrink")

    assert not sink.of("skip")
    converted = sink.of("converted")
    assert len(converted) == 1
    assert converted[0]["output"] == os.path.join(str(tmp_path), "movie_av1.mp4")
    assert os.path.isfile(converted[0]["output"])


def test_shrink_result_fields(tmp_path, shrink_env):
    src = _make_clip(tmp_path / "in.mp4", w=640, h=480, rate=10, duration=1)
    res = convert_file(src, output_path(src, suffix="_av1"), mode="shrink")
    assert res["mode"] == "shrink"
    assert res["encoder"] == "svtav1"
    assert res["speed"] == "quality"
    assert res["size_out"] < res["size_in"]

    res_fast = convert_file(src, output_path(src, suffix="_av1"), mode="shrink", speed="fast")
    assert res_fast["speed"] == "fast"
    assert res_fast["output"].endswith("in_av1_1.mp4")


def test_output_path_suffix(tmp_path):
    src = tmp_path / "x.mp4"
    src.write_bytes(b"")
    assert output_path(str(src), suffix="_av1") == str(tmp_path / "x_av1.mp4")
    (tmp_path / "x_av1.mp4").write_bytes(b"")
    assert output_path(str(src), suffix="_av1") == str(tmp_path / "x_av1_1.mp4")


@pytest.mark.parametrize("enc,speed,args", [
    ("av1_nvenc", "quality", ["-c:v", "av1_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", "36",
                              "-b:v", "0", "-multipass", "fullres", "-spatial-aq", "1", "-rc-lookahead", "32",
                              "-highbitdepth", "1", "-pix_fmt", "p010le"]),
    ("av1_nvenc", "fast", ["-c:v", "av1_nvenc", "-preset", "p1", "-rc", "vbr", "-cq", "36", "-b:v", "0",
                           "-highbitdepth", "1", "-pix_fmt", "p010le"]),
    ("svtav1", "quality", ["-c:v", "libsvtav1", "-preset", "8", "-crf", "35", "-pix_fmt", "yuv420p10le"]),
    ("svtav1", "fast", ["-c:v", "libsvtav1", "-preset", "10", "-crf", "35", "-pix_fmt", "yuv420p10le"]),
])
def test_shrink_video_args(enc, speed, args):
    # Command-builder only; no encode, so no GPU is needed.
    cmd = convert._ffmpeg_cmd("in.mp4", "out.mp4.partial", "shrink", enc, speed, ["fps=60"])
    i = cmd.index("-c:v")
    assert cmd[i:i + len(args)] == args
    assert cmd[cmd.index("-vf") + 1] == "fps=60"
    assert cmd[cmd.index("-map_metadata") + 1] == "0"
    assert cmd[cmd.index("-c:a"):cmd.index("-c:a") + 4] == ["-c:a", "aac", "-b:a", "128k"]
    assert cmd[-1] == "out.mp4.partial"


@pytest.mark.parametrize("w,h,fps,expected", [
    (2560, 1440, 30.0, ["scale=-2:1080"]),
    (2560, 1440, 120.0, ["scale=-2:1080", "fps=60"]),
    (2160, 3840, 30.0, ["scale=1080:-2"]),
    (1080, 1920, 30.0, []),
    (1280, 720, 30.0, []),       # never upscale
    (1280, 720, 120.0, ["fps=60"]),
    (1920, 1080, 60.5, []),      # 60.5 is not above the cap
    (1920, 1080, None, []),
])
def test_shrink_filters(w, h, fps, expected):
    assert convert._shrink_filters({"width": w, "height": h, "fps": fps}) == expected


def test_shrink_precheck_codecs():
    base = {"width": 1920, "height": 1080, "fps": 30.0, "hdr": False}
    with pytest.raises(ConvertSkipped, match="already HEVC/AV1"):
        convert._shrink_precheck({**base, "codec": "av1"})
    with pytest.raises(ConvertSkipped, match="already HEVC/AV1"):
        convert._shrink_precheck({**base, "codec": "hevc", "fps": None})
    # HEVC above the cap is still shrunk (it gets scaled).
    convert._shrink_precheck({**base, "codec": "hevc", "width": 2560, "height": 1440})
    convert._shrink_precheck({**base, "codec": "h264"})


def test_copy_and_reencode_ffmpeg_commands_unchanged():
    # Baseline recorded before shrink was added. Copy and reencode output must not change.
    def tail(mode, enc, speed="quality"):
        return convert._ffmpeg_cmd("in.ts", "out.mp4.partial", mode, enc, speed)[1:]

    assert tail("copy", "copy") == [
        "-v", "error", "-nostdin", "-y", "-i", "in.ts", "-map", "0:v:0", "-map", "0:a?", "-c", "copy",
        "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", "-f", "mp4", "out.mp4.partial"]
    assert tail("reencode", "nvenc", "quality") == [
        "-v", "error", "-nostdin", "-y", "-i", "in.ts", "-map", "0:v:0", "-map", "0:a?",
        "-c:a", "aac", "-b:a", "192k", "-c:v", "h264_nvenc", "-preset", "p5", "-cq", "23",
        "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", "-f", "mp4", "out.mp4.partial"]
    assert tail("reencode", "nvenc", "fast") == [
        "-v", "error", "-nostdin", "-y", "-i", "in.ts", "-map", "0:v:0", "-map", "0:a?",
        "-c:a", "aac", "-b:a", "192k", "-c:v", "h264_nvenc", "-preset", "p1", "-cq", "23",
        "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", "-f", "mp4", "out.mp4.partial"]
    assert tail("reencode", "x264", "quality") == [
        "-v", "error", "-nostdin", "-y", "-i", "in.ts", "-map", "0:v:0", "-map", "0:a?",
        "-c:a", "aac", "-b:a", "192k", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", "-f", "mp4", "out.mp4.partial"]
    assert tail("reencode", "x264", "fast") == [
        "-v", "error", "-nostdin", "-y", "-i", "in.ts", "-map", "0:v:0", "-map", "0:a?",
        "-c:a", "aac", "-b:a", "192k", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", "-f", "mp4", "out.mp4.partial"]
