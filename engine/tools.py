"""ffmpeg/ffprobe lookup and a small subprocess wrapper."""
import os
import shutil
import subprocess


def _find(env_name: str, name: str) -> str:
    return os.environ.get(env_name) or shutil.which(name) or name


def ffmpeg_bin() -> str:
    return _find("FFMPEG_BIN", "ffmpeg")


def ffprobe_bin() -> str:
    return _find("FFPROBE_BIN", "ffprobe")


def run(cmd: list[str], timeout: float = 60) -> subprocess.CompletedProcess:
    """Run a command, capturing bytes. Never raises on non-zero exit."""
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.run(cmd, capture_output=True, timeout=timeout, check=False, **kwargs)
