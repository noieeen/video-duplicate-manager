import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _run(db, *args):
    proc = subprocess.run(
        [sys.executable, "-m", "engine", "--db", str(db), *args],
        cwd=REPO, capture_output=True, timeout=600,
    )
    stdout = proc.stdout.decode("utf-8")
    events = [json.loads(line) for line in stdout.splitlines() if line.strip()]
    return proc.returncode, events


def _of(events, type):
    return [e for e in events if e["type"] == type]


def test_cli_round_trip(tmp_path, make_video):
    root = tmp_path / "root"
    root_a, root_b = root / "A", root / "B"
    hd = make_video(root_a / "a" / "clip.mp4", source="testsrc", w=640, h=480, duration=30)
    sd = make_video(root_b / "clip.mp4", source="testsrc", w=320, h=240, duration=30)
    db = tmp_path / "lib" / "library.db"
    dup_root = tmp_path / "dups"

    code, events = _run(db, "scan", "--root", str(root_a), "--root", str(root_b))
    assert code == 0
    assert _of(events, "scan_done")

    code, events = _run(db, "groups")
    assert code == 0
    groups = _of(events, "groups")
    assert len(groups) == 1
    assert len(groups[0]["groups"]) == 1
    group = groups[0]["groups"][0]
    keeper = [m for m in group["members"] if m["role"] == "keeper"][0]
    assert os.path.normcase(keeper["path"]) == os.path.normcase(os.path.abspath(hd))

    code, events = _run(db, "confirm", "--all-pending")
    assert code == 0
    assert _of(events, "ok")[0]["confirmed"] == 1

    code, events = _run(db, "apply", "--dup-root", str(dup_root))
    assert code == 0
    done = _of(events, "apply_done")[0]
    assert done["moved"] == 1 and done["failed"] == 0
    assert not os.path.exists(sd)

    code, events = _run(db, "history")
    assert code == 0
    batches = _of(events, "history")[0]["batches"]
    assert len(batches) == 1
    assert batches[0]["batch_id"] == done["batch_id"]
    assert batches[0]["moved"] == 1

    code, events = _run(db, "undo", "--batch", done["batch_id"])
    assert code == 0
    assert _of(events, "undo_done")[0]["restored"] == 1
    assert os.path.isfile(sd)


def test_missing_root_exits_1_with_error_event(tmp_path):
    db = tmp_path / "lib" / "library.db"
    code, events = _run(db, "scan", "--root", str(tmp_path / "does-not-exist"))
    assert code == 1
    errors = _of(events, "error")
    assert len(errors) == 1
    assert errors[0]["message"]
