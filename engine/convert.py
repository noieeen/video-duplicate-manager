"""Convert videos to MP4: lossless remux (copy), re-encode, or shrink to AV1. Contract: docs/ENGINE_SPEC.md, "Convert to MP4".

Output goes to `<dst>.partial` first and is renamed to `dst` only after verification. Nothing is ever
overwritten and the source is never deleted.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Callable

from engine import tools
from engine.probe import ProbeError, probe
from engine.queries import latest_scan_id
from engine.scan import VIDEO_EXTS

MODES = ("copy", "reencode", "shrink")
ENCODERS = ("auto", "nvenc", "x264")
SPEEDS = ("quality", "fast")

# Shrink: a result that is not at least this much smaller than the source is discarded (ConvertSkipped).
KEEP_RATIO = 0.85
_SHRINK_SHORT_SIDE = 1080
_SHRINK_MAX_FPS = 60.5

# Keyed by (encoder, speed). "fast" trades ~9% file size for speed; the NVENC encoder is a single
# GPU block, so the preset is the only speed lever there.
_VIDEO_ARGS = {
    ("nvenc", "quality"): ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "23"],
    ("nvenc", "fast"): ["-c:v", "h264_nvenc", "-preset", "p1", "-cq", "23"],
    ("x264", "quality"): ["-c:v", "libx264", "-preset", "medium", "-crf", "20"],
    ("x264", "fast"): ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"],
}

# Shrink video args, keyed by (AV1 encoder from _av1_encoder(), speed).
_AV1_VIDEO_ARGS = {
    ("av1_nvenc", "quality"): ["-c:v", "av1_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", "36",
                               "-b:v", "0", "-multipass", "fullres", "-spatial-aq", "1", "-rc-lookahead", "32",
                               "-highbitdepth", "1", "-pix_fmt", "p010le"],
    ("av1_nvenc", "fast"): ["-c:v", "av1_nvenc", "-preset", "p1", "-rc", "vbr", "-cq", "36", "-b:v", "0",
                            "-highbitdepth", "1", "-pix_fmt", "p010le"],
    ("svtav1", "quality"): ["-c:v", "libsvtav1", "-preset", "8", "-crf", "35", "-pix_fmt", "yuv420p10le"],
    ("svtav1", "fast"): ["-c:v", "libsvtav1", "-preset", "10", "-crf", "35", "-pix_fmt", "yuv420p10le"],
}

_nvenc_cache: bool | None = None
_av1_cache: str | None = None


class ConvertError(Exception):
    pass


class ConvertSkipped(ConvertError):
    """A deliberate skip with a human-readable reason. Not a failure."""


def _popen_kwargs() -> dict:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def _probe_nvenc() -> bool:
    ff = tools.ffmpeg_bin()
    try:
        listing = tools.run([ff, "-hide_banner", "-encoders"], timeout=30)
        if listing.returncode != 0 or b"h264_nvenc" not in listing.stdout:
            return False
        test = tools.run([ff, "-v", "error", "-f", "lavfi", "-i", "color=s=256x256:d=1",
                          "-frames:v", "1", "-c:v", "h264_nvenc", "-f", "null", "-"], timeout=30)
        return test.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def nvenc_available() -> bool:
    """True when ffmpeg lists h264_nvenc and a 1-frame test encode succeeds. Cached per process."""
    global _nvenc_cache
    if _nvenc_cache is None:
        _nvenc_cache = _probe_nvenc()
    return _nvenc_cache


def _probe_av1_nvenc() -> bool:
    ff = tools.ffmpeg_bin()
    try:
        listing = tools.run([ff, "-hide_banner", "-encoders"], timeout=30)
        if listing.returncode != 0 or b"av1_nvenc" not in listing.stdout:
            return False
        test = tools.run([ff, "-v", "error", "-f", "lavfi", "-i", "color=s=256x256:d=1",
                          "-frames:v", "1", "-c:v", "av1_nvenc", "-f", "null", "-"], timeout=30)
        return test.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _lists_encoder(name: str) -> bool:
    try:
        listing = tools.run([tools.ffmpeg_bin(), "-hide_banner", "-encoders"], timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return listing.returncode == 0 and name.encode() in listing.stdout


def _av1_encoder() -> str:
    """"av1_nvenc" when listed and a 1-frame test encode works, else "svtav1" (libsvtav1). Cached per process."""
    global _av1_cache
    if _av1_cache is None:
        if _probe_av1_nvenc():
            _av1_cache = "av1_nvenc"
        elif _lists_encoder("libsvtav1"):
            _av1_cache = "svtav1"
        else:
            raise ConvertError("no AV1 encoder available (needs av1_nvenc or libsvtav1)")
    return _av1_cache


def _same_file(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def output_path(src: str, out_dir: str | None = None, suffix: str = "") -> str:
    """`<out_dir or dirname(src)>/<stem><suffix>.mp4`, or `<stem><suffix>_1.mp4`, `_2`, ... Never returns an existing path."""
    folder = out_dir or os.path.dirname(os.path.abspath(src))
    stem = os.path.splitext(os.path.basename(src))[0] + suffix
    n = 0
    while True:
        name = f"{stem}.mp4" if n == 0 else f"{stem}_{n}.mp4"
        candidate = os.path.join(folder, name)
        if not os.path.exists(candidate) and not _same_file(candidate, src):
            return candidate
        n += 1


def _resolve_encoder(mode: str, encoder: str) -> str:
    if mode == "copy":
        return "copy"
    if mode == "shrink":
        return _av1_encoder()  # the `encoder` argument does not apply to AV1
    if encoder == "nvenc":
        if not nvenc_available():
            raise ConvertError("NVIDIA encoder (h264_nvenc) is not available on this machine")
        return "nvenc"
    if encoder == "auto" and nvenc_available():
        return "nvenc"
    return "x264"


def _shrink_precheck(meta: dict) -> None:
    if meta["hdr"]:
        raise ConvertSkipped("HDR source (Shrink keeps SDR only)")
    fps = meta["fps"]
    if (meta["codec"] in ("hevc", "av1") and min(meta["width"], meta["height"]) <= _SHRINK_SHORT_SIDE
            and (fps is None or fps <= _SHRINK_MAX_FPS)):
        raise ConvertSkipped("already HEVC/AV1 within 1080p/60 fps")


def _shrink_filters(meta: dict) -> list[str]:
    """Cap the short side at 1080 (never upscale) and fps at 60 (never raise). Empty when nothing to do."""
    filters = []
    w, h = meta["width"], meta["height"]
    if min(w, h) > _SHRINK_SHORT_SIDE:
        filters.append("scale=-2:1080" if w >= h else "scale=1080:-2")
    if meta["fps"] is not None and meta["fps"] > _SHRINK_MAX_FPS:
        filters.append("fps=60")
    return filters


def _ffmpeg_cmd(src: str, partial: str, mode: str, enc: str, speed: str = "quality",
                filters: list[str] | tuple[str, ...] = ()) -> list[str]:
    cmd = [tools.ffmpeg_bin(), "-v", "error", "-nostdin", "-y", "-i", src,
           "-map", "0:v:0", "-map", "0:a?"]
    if mode == "copy":
        cmd += ["-c", "copy"]
    elif mode == "shrink":
        cmd += ["-map_metadata", "0"]
        if filters:
            cmd += ["-vf", ",".join(filters)]
        cmd += _AV1_VIDEO_ARGS[(enc, speed)] + ["-c:a", "aac", "-b:a", "128k"]
    else:
        cmd += ["-c:a", "aac", "-b:a", "192k"] + _VIDEO_ARGS[(enc, speed)]
    cmd += ["-movflags", "+faststart", "-progress", "pipe:1", "-nostats", "-f", "mp4", partial]
    return cmd


def _run_ffmpeg(cmd: list[str], duration: float, on_percent: Callable[[int], None] | None) -> None:
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, **_popen_kwargs())
    last = -1
    try:
        if on_percent:
            on_percent(0)
            last = 0
        for raw in proc.stdout:
            key, _, value = raw.decode("utf-8", "replace").strip().partition("=")
            # out_time_ms is also microseconds in ffmpeg; "N/A" and negative values are ignored.
            if key not in ("out_time_us", "out_time_ms") or not value.isdigit():
                continue
            pct = min(99, int(int(value) * 100 / (duration * 1_000_000)))
            if pct > last:
                last = pct
                if on_percent:
                    on_percent(pct)
        stderr = proc.stderr.read()
        rc = proc.wait()
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    finally:
        proc.stdout.close()
        proc.stderr.close()
    if rc != 0:
        raise ConvertError(stderr.decode("utf-8", "replace").strip() or f"ffmpeg exit {rc}")


def _verify(path: str, src_duration: float) -> None:
    try:
        out = probe(path)
    except ProbeError as e:
        raise ConvertError(f"output failed verification: {e}") from e
    tol = max(1.0, 0.01 * src_duration)
    if abs(out["duration"] - src_duration) > tol:
        raise ConvertError(
            f"output duration {out['duration']:.2f}s differs from source {src_duration:.2f}s")


def _remove_quietly(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def convert_file(src: str, dst: str, mode: str = "copy", encoder: str = "auto",
                 on_percent: Callable[[int], None] | None = None, speed: str = "quality") -> dict:
    """Convert one file to `dst` (must not exist). Raises ConvertError; on failure no output is left behind.

    `speed` applies to re-encode and shrink; the result's "speed" is None only in copy mode.
    Shrink raises ConvertSkipped (no output left) for HDR sources, already-HEVC/AV1 sources within the cap,
    and outputs that are not at least 15% smaller.
    """
    if mode not in MODES:
        raise ConvertError(f"unknown mode: {mode}")
    if encoder not in ENCODERS:
        raise ConvertError(f"unknown encoder: {encoder}")
    if speed not in SPEEDS:
        raise ConvertError(f"unknown speed: {speed}")
    if not os.path.isfile(src):
        raise ConvertError(f"source not found: {src}")
    if os.path.exists(dst):
        raise ConvertError(f"destination already exists: {dst}")
    try:
        meta = probe(src)
    except ProbeError as e:
        raise ConvertError(f"cannot read source: {e}") from e
    filters: list[str] = []
    if mode == "shrink":
        _shrink_precheck(meta)
        filters = _shrink_filters(meta)

    enc = _resolve_encoder(mode, encoder)
    partial = dst + ".partial"
    t0 = time.monotonic()
    _remove_quietly(partial)
    try:
        os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
        _run_ffmpeg(_ffmpeg_cmd(src, partial, mode, enc, speed, filters), meta["duration"], on_percent)
        _verify(partial, meta["duration"])
        if mode == "shrink" and os.path.getsize(partial) > KEEP_RATIO * os.path.getsize(src):
            pct = int(100 * os.path.getsize(partial) / os.path.getsize(src))
            raise ConvertSkipped(f"not smaller: output was {pct}% of the original (needs <= 85%)")
        if os.path.exists(dst):
            raise ConvertError(f"destination appeared during conversion: {dst}")
        os.replace(partial, dst)
    except ConvertError:
        _remove_quietly(partial)
        raise
    except Exception as e:
        _remove_quietly(partial)
        raise ConvertError(str(e) or type(e).__name__) from e
    except BaseException:
        _remove_quietly(partial)
        raise

    if on_percent:
        on_percent(100)
    return {
        "input": src,
        "output": dst,
        "mode": mode,
        "encoder": enc,
        "speed": None if mode == "copy" else speed,
        "seconds": round(time.monotonic() - t0, 2),
        "size_in": os.path.getsize(src),
        "size_out": os.path.getsize(dst),
    }


def run_convert(conn, inputs: list[str], emit, mode: str = "copy", encoder: str = "auto",
                out_dir: str | None = None, speed: str = "quality") -> dict:
    """Convert inputs one after another. Skips missing, duplicate and (copy/reencode) .mp4 inputs. Returns the convert_done summary.

    `conn` is unused here (the CLI resolves --from-scan-ext before calling).
    """
    if mode not in MODES:
        raise ConvertError(f"unknown mode: {mode}")
    if encoder not in ENCODERS:
        raise ConvertError(f"unknown encoder: {encoder}")
    if speed not in SPEEDS:
        raise ConvertError(f"unknown speed: {speed}")

    total = len(inputs)
    emit("convert_started", total=total, mode=mode)
    converted = failed = skipped = 0
    outputs: list[str] = []
    seen: set[str] = set()

    for path in inputs:
        key = os.path.normcase(os.path.abspath(path))
        if key in seen:
            reason = "duplicate input"
        elif not os.path.isfile(path):
            reason = "file not found"
        elif mode != "shrink" and path.lower().endswith(".mp4"):
            reason = "already mp4"
        else:
            reason = None
        seen.add(key)
        if reason:
            emit("skip", path=path, reason=reason)
            skipped += 1
            continue

        done = converted + failed + skipped

        def on_percent(pct: int, path=path, done=done) -> None:
            emit("progress", phase="convert", done=done, total=total, path=path, percent=pct)

        try:
            res = convert_file(path, output_path(path, out_dir, "_av1" if mode == "shrink" else ""), mode,
                               encoder, on_percent=on_percent, speed=speed)
        except ConvertSkipped as e:
            emit("skip", path=path, reason=str(e))
            skipped += 1
            continue
        except Exception as e:  # one bad file must not stop the batch
            emit("file_error", path=path, error=str(e) or type(e).__name__)
            failed += 1
            continue
        emit("converted", **res)
        converted += 1
        outputs.append(res["output"])

    summary = {"converted": converted, "failed": failed, "skipped": skipped, "outputs": outputs}
    emit("convert_done", **summary)
    return summary


def video_files_under(roots: list[str], ext: str | None = None) -> list[str]:
    """Video files found on disk under the given folders (recursive), not read from the DB.

    Default extensions: scan.VIDEO_EXTS minus ".mp4". With `ext`, only that extension. Matching is
    case-insensitive. Returns sorted, de-duplicated realpaths. Raises ValueError if a root is not a directory.
    """
    for root in roots:
        if not os.path.isdir(root):
            raise ValueError(f"root is not a directory: {root}")
    if ext is None:
        exts = VIDEO_EXTS - {".mp4"}
    else:
        e = ext.lower()
        exts = {e if e.startswith(".") else "." + e}
    found: set[str] = set()
    for root in roots:
        for dirpath, _dirs, names in os.walk(root):
            for name in names:
                if os.path.splitext(name)[1].lower() in exts:
                    found.add(os.path.realpath(os.path.join(dirpath, name)))
    return sorted(found)


def ok_files_with_ext(conn, ext: str) -> list[str]:
    """Files with status 'ok' under the roots of the latest done scan whose path ends with ext (case-insensitive)."""
    scan_id = latest_scan_id(conn)
    if scan_id is None:
        return []
    row = conn.execute("SELECT roots FROM scans WHERE id=?", (scan_id,)).fetchone()
    roots = json.loads(row["roots"])
    if not roots:
        return []
    if not ext.startswith("."):
        ext = "." + ext
    ph = ",".join("?" * len(roots))
    rows = conn.execute(
        f"SELECT path FROM files WHERE status='ok' AND root IN ({ph})", roots
    ).fetchall()
    suffix = ext.lower()
    return sorted(r["path"] for r in rows if r["path"].lower().endswith(suffix))
