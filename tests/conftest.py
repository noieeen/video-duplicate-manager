"""Shared fixtures: synthetic videos generated with ffmpeg (cached per test session)."""
import os
import shutil
import subprocess

import pytest

SOURCES = {
    "testsrc": "testsrc=size={w}x{h}:rate=2",
    "testsrc2": "testsrc2=size={w}x{h}:rate=2",
    "mandelbrot": "mandelbrot=size={w}x{h}:rate=2",
    "smpte": "smptebars=size={w}x{h}:rate=2",
    "black": "color=c=black:size={w}x{h}:rate=2",
}


@pytest.fixture(scope="session")
def _video_cache(tmp_path_factory):
    return tmp_path_factory.mktemp("video_cache")


@pytest.fixture(scope="session")
def make_video(_video_cache):
    """make_video(dest_path, source="testsrc", w=320, h=240, duration=60, ext=None) -> str

    Renders (once per unique spec) and copies to dest_path. Container follows dest_path's extension.
    `source` is a key of SOURCES. Same source + duration at different sizes = "same video, re-encoded".
    """
    def _make(dest, source="testsrc", w=320, h=240, duration=60):
        dest = str(dest)
        ext = os.path.splitext(dest)[1].lower() or ".mp4"
        key = f"{source}_{w}x{h}_{duration}{ext}"
        cached = _video_cache / key
        if not cached.exists():
            lavfi = SOURCES[source].format(w=w, h=h)
            cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", lavfi, "-t", str(duration),
                   "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast", str(cached)]
            subprocess.run(cmd, check=True)
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        shutil.copyfile(cached, dest)
        return dest
    return _make
