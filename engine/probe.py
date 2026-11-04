"""Read video metadata with a single ffprobe call."""
import json
import subprocess

from engine import tools


class ProbeError(Exception):
    pass


def _positive_int(value) -> int | None:
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def probe(path: str) -> dict:
    cmd = [
        tools.ffprobe_bin(), "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,codec_name,bit_rate:format=duration,bit_rate",
        "-of", "json", path,
    ]
    try:
        r = tools.run(cmd, timeout=60)
    except subprocess.TimeoutExpired as e:
        raise ProbeError(f"ffprobe timed out: {path}") from e
    except OSError as e:
        raise ProbeError(f"ffprobe could not start: {e}") from e

    if r.returncode != 0:
        msg = r.stderr.decode("utf-8", "replace").strip()
        raise ProbeError(msg or f"ffprobe exit {r.returncode}")
    try:
        data = json.loads(r.stdout.decode("utf-8", "replace") or "{}")
    except json.JSONDecodeError as e:
        raise ProbeError(f"ffprobe returned invalid JSON: {e}") from e

    streams = data.get("streams") or []
    if not streams:
        raise ProbeError("no video stream")
    stream = streams[0]
    fmt = data.get("format") or {}

    try:
        duration = float(fmt.get("duration"))
    except (TypeError, ValueError):
        raise ProbeError("duration missing")
    if duration <= 0:
        raise ProbeError("duration not positive")

    width = _positive_int(stream.get("width"))
    height = _positive_int(stream.get("height"))
    if width is None or height is None:
        raise ProbeError("dimensions missing")

    # MPEG-TS often reports "N/A" for stream bit_rate; fall back to the container's.
    bit_rate = _positive_int(stream.get("bit_rate")) or _positive_int(fmt.get("bit_rate")) or 0

    return {
        "duration": duration,
        "width": width,
        "height": height,
        "bit_rate": bit_rate,
        "codec": stream.get("codec_name") or "",
    }
