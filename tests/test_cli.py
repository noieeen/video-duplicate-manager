import json
import os
import subprocess
import sqlite3
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


def test_cli_convert_rejects_unknown_speed(tmp_path):
    db = tmp_path / "lib" / "library.db"
    proc = subprocess.run(
        [sys.executable, "-m", "engine", "--db", str(db), "convert", "--input", "x.ts", "--speed", "bogus"],
        cwd=REPO, capture_output=True, timeout=600,
    )
    assert proc.returncode == 2


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


def test_apply_without_dup_root_or_trash_is_rejected(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-m", "engine", "--db", str(tmp_path / "lib.db"), "apply"],
        cwd=REPO, capture_output=True, timeout=600,
    )
    assert proc.returncode == 2
    assert b"--dup-root" in proc.stderr


def test_apply_trash_runs_through_cli_with_fake_bin(tmp_path, make_video, monkeypatch, capsys):
    from engine import apply as apply_mod
    from engine import cli, db, queries
    from engine import scan as scan_mod

    root_a, root_b = tmp_path / "rootA", tmp_path / "rootB"
    hd = make_video(root_a / "a" / "clip.mp4", source="testsrc", w=640, h=480, duration=30)
    sd = make_video(root_b / "clip.mp4", source="testsrc", w=320, h=240, duration=30)
    db_path = tmp_path / "lib" / "library.db"
    conn = db.connect(str(db_path))
    scan_mod.run_scan(conn, [str(root_a), str(root_b)], lambda *a, **k: None)
    queries.confirm_groups(conn, None, queries.latest_scan_id(conn))
    conn.close()
    sd_size = os.path.getsize(sd)

    deleted = []

    def fake(path):  # never touch the real Recycle Bin
        deleted.append(path)
        os.remove(path)

    monkeypatch.setattr(apply_mod, "_send_to_trash", fake)
    assert cli.main(["--db", str(db_path), "apply", "--trash"]) == 0

    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    done = _of(events, "apply_done")[0]
    assert done["moved"] == 1 and done["failed"] == 0 and done["mode"] == "trash"
    assert done["bytes"] == sd_size
    assert len(deleted) == 1 and not os.path.exists(sd)
    assert os.path.isfile(hd)


def test_scan_exclude_reaches_scan_settings(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    db = tmp_path / "lib" / "library.db"

    code, _ = _run(db, "scan", "--root", str(empty), "--exclude", "*.tmp", "--exclude", "Trash")
    assert code == 0

    settings = _settings(db)
    assert settings["exclude"] == ["*.tmp", "Trash"]


def test_scan_without_exclude_sends_empty_list(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    db = tmp_path / "lib" / "library.db"

    code, _ = _run(db, "scan", "--root", str(empty))
    assert code == 0

    assert _settings(db)["exclude"] == []


def _add_library_rows(db_path):
    from engine import db as db_mod

    conn = db_mod.connect(str(db_path))
    rows = [
        ("/v/big.mp4", 500, 2560, 1440, "h264", 8_000_000),
        ("/v/low.mp4", 300, 1280, 720, "mpeg4", 2_000_000),
        ("/v/hevc.mp4", 100, 1920, 1080, "hevc", 9_000_000),
    ]
    for path, size, w, h, codec, bit_rate in rows:
        conn.execute(
            "INSERT INTO files(path, root, size, mtime, width, height, codec, bit_rate, status) "
            "VALUES (?,?,?,?,?,?,?,?,'ok')",
            (path, "/v", size, 0.0, w, h, codec, bit_rate),
        )
    conn.commit()
    conn.close()


def test_cli_library_summary_and_group(tmp_path):
    db = tmp_path / "lib" / "library.db"
    _add_library_rows(db)

    code, events = _run(db, "library")
    assert code == 0
    summary = _of(events, "library")
    assert len(summary) == 1 and not _of(events, "library_files")
    assert summary[0]["group_by"] == "potential"
    assert summary[0]["total_files"] == 3
    assert [g["key"] for g in summary[0]["groups"]] == ["above_1080p", "old_low", "efficient"]

    code, events = _run(db, "library", "--group", "old_low", "--limit", "1")
    assert code == 0
    listed = _of(events, "library_files")
    assert len(listed) == 1 and not _of(events, "library")
    assert listed[0]["key"] == "old_low"
    assert listed[0]["total"] == 1
    assert [f["path"] for f in listed[0]["files"]] == ["/v/low.mp4"]


def test_cli_library_group_by_codec(tmp_path):
    db = tmp_path / "lib" / "library.db"
    _add_library_rows(db)

    code, events = _run(db, "library", "--group-by", "codec")
    assert code == 0
    summary = _of(events, "library")[0]
    assert summary["group_by"] == "codec"
    assert [g["key"] for g in summary["groups"]] == ["h264", "mpeg4", "hevc"]


def _settings(db):
    conn = sqlite3.connect(str(db))
    try:
        raw = conn.execute("SELECT settings FROM scans ORDER BY id DESC LIMIT 1").fetchone()[0]
    finally:
        conn.close()
    return json.loads(raw)
