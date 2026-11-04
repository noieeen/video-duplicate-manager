"""Tests for engine/maintenance.py (clear-cache) and the `clear-cache` / `scan --fresh` CLI."""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from engine import apply as apply_mod
from engine import db, maintenance, queries
from engine import scan as scan_mod
from engine.maintenance import TS_FORMAT

REPO = Path(__file__).resolve().parents[1]


def _emitter():
    events = []

    def emit(type, **data):
        events.append({"type": type, **data})

    return events, emit


def _norm(p):
    return os.path.realpath(p)


def _setup(tmp_path, make_video):
    """Duplicate pair (HD keeper in rootA, SD in rootB) plus one unrelated video. Scanned once."""
    root_a, root_b = tmp_path / "rootA", tmp_path / "rootB"
    hd = make_video(root_a / "a" / "clip.mp4", source="testsrc", w=640, h=480, duration=30)
    sd = make_video(root_b / "clip.mp4", source="testsrc", w=320, h=240, duration=30)
    make_video(root_a / "solo.mp4", source="mandelbrot", w=320, h=240, duration=30)
    conn = db.connect(str(tmp_path / "lib" / "library.db"))
    thumbs = str(tmp_path / "lib" / "thumbs")
    roots = [str(root_a), str(root_b)]
    _, emit = _emitter()
    scan_id = scan_mod.run_scan(conn, roots, emit, thumbs_dir=thumbs)
    return conn, {
        "hd": hd, "sd": sd, "roots": roots, "thumbs": thumbs, "scan_id": scan_id,
        "dup_root": str(tmp_path / "dups"), "lib": str(tmp_path / "lib"),
    }


def _group_paths(conn, scan_id):
    return {frozenset(m["path"] for m in g["members"]) for g in queries.groups_json(conn, scan_id)}


def _jpgs(folder):
    return sorted(n for n in os.listdir(folder) if n.endswith(".jpg"))


def test_clear_cache_then_undo_then_rescan(tmp_path, make_video):
    conn, h = _setup(tmp_path, make_video)
    _, emit = _emitter()
    groups_before = _group_paths(conn, h["scan_id"])
    assert len(groups_before) == 1
    assert _jpgs(h["thumbs"])

    gid = queries.groups_json(conn, h["scan_id"])[0]["id"]
    queries.set_group_status(conn, gid, "confirmed")
    batch = apply_mod.apply_groups(conn, None, h["dup_root"], emit)
    assert queries.groups_json(conn, status="applied")

    sd_id = conn.execute("SELECT id FROM files WHERE path=?", (_norm(h["sd"]),)).fetchone()[0]
    actions_before = conn.execute("SELECT COUNT(*) FROM actions").fetchone()[0]
    history_before = queries.history_json(conn)
    dup_group_before = [tuple(r) for r in conn.execute("SELECT id, status FROM dup_groups ORDER BY id")]
    fp_before = conn.execute("SELECT COUNT(*) FROM fingerprints").fetchone()[0]
    assert fp_before > 0

    # a non-jpg file in the thumbs folder must survive
    notes = os.path.join(h["thumbs"], "notes.txt")
    with open(notes, "w", encoding="utf-8") as f:
        f.write("keep me")

    result = maintenance.clear_cache(conn, h["thumbs"])

    assert result["fingerprints"] == fp_before
    assert result["files_reset"] == 2  # the keeper and the unrelated video (the moved file is untouched)
    assert result["thumbs_deleted"] >= 1
    assert conn.execute("SELECT COUNT(*) FROM fingerprints").fetchone()[0] == 0

    hd_row = conn.execute("SELECT * FROM files WHERE path=?", (_norm(h["hd"]),)).fetchone()
    assert hd_row["status"] == "new"
    for col in ("duration", "width", "height", "bit_rate", "codec", "score", "thumb", "probed_at", "error"):
        assert hd_row[col] is None, col

    assert conn.execute("SELECT status FROM files WHERE id=?", (sd_id,)).fetchone()[0] == "moved"
    assert conn.execute("SELECT COUNT(*) FROM actions").fetchone()[0] == actions_before
    assert queries.history_json(conn) == history_before
    assert [tuple(r) for r in conn.execute("SELECT id, status FROM dup_groups ORDER BY id")] == dup_group_before

    assert _jpgs(h["thumbs"]) == []
    assert os.path.isfile(notes)

    # History/Undo keep working after the cache is cleared
    undone = apply_mod.undo_batch(conn, batch, emit)
    assert undone == 1
    assert os.path.isfile(h["sd"])

    # The next normal scan re-reads everything and finds the same groups
    _, emit2 = _emitter()
    rescan = scan_mod.run_scan(conn, h["roots"], emit2, thumbs_dir=h["thumbs"])
    stats = json.loads(conn.execute("SELECT stats FROM scans WHERE id=?", (rescan,)).fetchone()[0])
    assert stats["cached"] == 0
    assert _group_paths(conn, rescan) == groups_before
    conn.close()


def test_clear_cache_refused_while_scan_running_recently(tmp_path, make_video):
    conn, h = _setup(tmp_path, make_video)
    conn.execute(
        "INSERT INTO scans(started_at, roots, settings, status) VALUES(?, '[]', '{}', 'running')",
        (db.now(),),
    )
    conn.commit()
    fp_before = conn.execute("SELECT COUNT(*) FROM fingerprints").fetchone()[0]
    thumbs_before = _jpgs(h["thumbs"])

    with pytest.raises(RuntimeError):
        maintenance.clear_cache(conn, h["thumbs"])

    assert conn.execute("SELECT COUNT(*) FROM fingerprints").fetchone()[0] == fp_before
    assert _jpgs(h["thumbs"]) == thumbs_before
    conn.close()


def test_clear_cache_allowed_when_running_scan_is_stale(tmp_path, make_video):
    conn, h = _setup(tmp_path, make_video)
    old = (datetime.now(timezone.utc) - timedelta(hours=7)).strftime(TS_FORMAT)
    conn.execute(
        "INSERT INTO scans(started_at, roots, settings, status) VALUES(?, '[]', '{}', 'running')",
        (old,),
    )
    conn.commit()

    result = maintenance.clear_cache(conn, h["thumbs"])
    assert result["fingerprints"] > 0
    conn.close()


def test_clear_cache_with_missing_thumbs_dir_deletes_nothing(tmp_path, make_video):
    conn, h = _setup(tmp_path, make_video)
    result = maintenance.clear_cache(conn, str(tmp_path / "no-such-thumbs"))
    assert result["thumbs_deleted"] == 0
    conn.close()


def _run(db_path, *args):
    proc = subprocess.run(
        [sys.executable, "-m", "engine", "--db", str(db_path), *args],
        cwd=REPO, capture_output=True, timeout=600,
    )
    events = [json.loads(line) for line in proc.stdout.decode("utf-8").splitlines() if line.strip()]
    return proc.returncode, events


def _of(events, type):
    return [e for e in events if e["type"] == type]


def test_cli_clear_cache_emits_cache_cleared(tmp_path, make_video):
    conn, h = _setup(tmp_path, make_video)
    conn.close()

    code, events = _run(h["lib"] + "/library.db", "clear-cache")
    assert code == 0
    cleared = _of(events, "cache_cleared")[0]
    assert set(cleared) == {"type", "fingerprints", "files_reset", "thumbs_deleted"}
    assert cleared["fingerprints"] > 0
    assert _jpgs(h["thumbs"]) == []


def test_cli_clear_cache_refused_is_error_exit_1(tmp_path, make_video):
    conn, h = _setup(tmp_path, make_video)
    conn.execute(
        "INSERT INTO scans(started_at, roots, settings, status) VALUES(?, '[]', '{}', 'running')",
        (db.now(),),
    )
    conn.commit()
    conn.close()

    code, events = _run(h["lib"] + "/library.db", "clear-cache")
    assert code == 1
    assert _of(events, "error")
    assert not _of(events, "cache_cleared")


def test_cli_scan_fresh_is_accepted(tmp_path, make_video):
    root = tmp_path / "root"
    make_video(root / "a.mp4", source="testsrc", w=320, h=240, duration=30)
    lib = tmp_path / "cli" / "library.db"

    code, events = _run(lib, "scan", "--root", str(root))
    assert code == 0
    code, events = _run(lib, "scan", "--root", str(root), "--fresh")
    assert code == 0
    stats = _of(events, "scan_done")[0]
    assert stats["cached"] == 0

    conn = db.connect(str(lib))
    settings = json.loads(conn.execute("SELECT settings FROM scans ORDER BY id DESC LIMIT 1").fetchone()[0])
    assert settings["fresh"] is True
    conn.close()
