import io
import json
import sys

from engine import tools
from engine.events import Emitter


def test_json_mode_line_parses():
    buf = io.StringIO()
    Emitter(stream=buf).emit("ok", confirmed=2)
    line = buf.getvalue()
    assert line.endswith("\n")
    ev = json.loads(line)
    assert ev["type"] == "ok"
    assert ev["confirmed"] == 2


def test_human_mode_contains_type():
    buf = io.StringIO()
    Emitter(human=True, stream=buf).emit("ok", confirmed=2)
    out = buf.getvalue()
    assert "ok" in out
    assert "confirmed=2" in out


def test_run_returns_bytes_and_does_not_raise_on_nonzero():
    r = tools.run([sys.executable, "-c", "import sys; sys.stdout.write('hi'); sys.exit(3)"])
    assert r.returncode == 3
    assert r.stdout == b"hi"
    assert isinstance(r.stderr, bytes)


def test_ffprobe_bin_is_string():
    assert isinstance(tools.ffprobe_bin(), str)
    assert tools.ffprobe_bin()
