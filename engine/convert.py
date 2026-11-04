"""Convert videos to MP4: lossless remux (copy) or re-encode. Contract: docs/ENGINE_SPEC.md, "Convert to MP4".

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

MODES = ("copy", "reencode")
ENCODERS = ("auto", "nvenc", "x264")

_VIDEO_ARGS = {
    "nvenc": ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "23"],
    "x264": ["-c:v", "libx264", "-preset", "medium", "-crf", "20"],
}

_nvenc_cache: bool | None = None


class ConvertError(Exception):
    pass


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


def _same_file(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def output_path(src: str, out_dir: str | None = None) -> str:
    """`<out_dir or dirname(src)>/<stem>.mp4`, or `<stem>_1.mp4`, `_2`, ... Never returns an existing path."""
    folder = out_dir or os.path.dirname(os.path.abspath(src))
    stem = os.path.splitext(os.path.basename(src))[0]
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
    if encoder == "nvenc":
        if not nvenc_available():
            raise ConvertError("NVIDIA encoder (h264_nvenc) is not available on this machine")
        return "nvenc"
    if encoder == "auto" and nvenc_available():
        return "nvenc"
    return "x264"


def _ffmpeg_cmd(src: str, partial: str, mode: str, enc: str) -> list[str]:
    cmd = [tools.ffmpeg_bin(), "-v", "error", "-nostdin", "-y", "-i", src,
           "-map", "0:v:0", "-map", "0:a?"]
    if mode == "copy":
        cmd += ["-c", "copy"]
    else:
        cmd += ["-c:a", "aac", "-b:a", "192k"] + _VIDEO_ARGS[enc]
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
                 on_percent: Callable[[int], None] | None = None) -> dict:
    """Convert one file to `dst` (must not exist). Raises ConvertError; on failure no output is left behind."""
    if mode not in MODES:
        raise ConvertError(f"unknown mode: {mode}")
    if encoder not in ENCODERS:
        raise ConvertError(f"unknown encoder: {encoder}")
    if not os.path.isfile(src):
        raise ConvertError(f"source not found: {src}")
    if os.path.exists(dst):
        raise ConvertError(f"destination already exists: {dst}")
    try:
        meta = probe(src)
    except ProbeError as e:
        raise ConvertError(f"cannot read source: {e}") from e

    enc = _resolve_encoder(mode, encoder)
    partial = dst + ".partial"
    t0 = time.monotonic()
    _remove_quietly(partial)
    try:
        os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
        _run_ffmpeg(_ffmpeg_cmd(src, partial, mode, enc), meta["duration"], on_percent)
        _verify(partial, meta["duration"])
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
        "seconds": round(time.monotonic() - t0, 2),
        "size_in": os.path.getsize(src),
        "size_out": os.path.getsize(dst),
    }


def run_convert(conn, inputs: list[str], emit, mode: str = "copy", encoder: str = "auto",
                out_dir: str | None = None) -> dict:
    """Convert inputs one after another. Skips missing, duplicate and .mp4 inputs. Returns the convert_done summary.

    `conn` is unused here (the CLI resolves --from-scan-ext before calling).
    """
    if mode not in MODES:
        raise ConvertError(f"unknown mode: {mode}")
    if encoder not in ENCODERS:
        raise ConvertError(f"unknown encoder: {encoder}")

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
        elif path.lower().endswith(".mp4"):
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
            res = convert_file(path, output_path(path, out_dir), mode, encoder, on_percent=on_percent)
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
