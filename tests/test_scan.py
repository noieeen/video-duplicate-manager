import hashlib
import json
import os

import pytest

from engine import db, probe as probe_mod, queries
from engine import scan as scan_mod


def _emitter():
    events = []

    def emit(type, **data):
        events.append({"type": type, **data})

    return events, emit


def _conn(tmp_path):
    return db.connect(str(tmp_path / "lib" / "library.db"))


def _norm(p):
    return os.path.realpath(p)


def _sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _file_row(conn, path):
    return conn.execute("SELECT * FROM files WHERE path=?", (_norm(path),)).fetchone()


def test_hd_and_sd_same_source_grouped_keeper_is_hd(tmp_path, make_video):
    root_a, root_b = tmp_path / "rootA", tmp_path / "rootB"
    hd = make_video(root_a / "a" / "clip.mp4", source="testsrc", w=640, h=480, duration=30)
    sd = make_video(root_b / "clip.mp4", source="testsrc", w=320, h=240, duration=30)
    other = make_video(root_a / "other.mp4", source="mandelbrot", w=640, h=480, duration=30)
    before = {p: _sha(p) for p in (hd, sd, other)}

    conn = _conn(tmp_path)
    _, emit = _emitter()
    scan_id = scan_mod.run_scan(conn, [str(root_a), str(root_b)], emit)

    groups = queries.groups_json(conn, scan_id)
    assert len(groups) == 1
    members = groups[0]["members"]
    assert {m["path"] for m in members} == {_norm(hd), _norm(sd)}
    keeper = [m for m in members if m["role"] == "keeper"]
    assert len(keeper) == 1
    assert keeper[0]["path"] == _norm(hd)
    assert _norm(other) not in {m["path"] for m in members}

    assert {p: _sha(p) for p in (hd, sd, other)} == before
    conn.close()


def test_ts_hd_beats_mp4_sd_keeper_is_ts(tmp_path, make_video):
    root_a, root_b = tmp_path / "rootA", tmp_path / "rootB"
    ts = make_video(root_a / "clip.ts", source="testsrc", w=640, h=480, duration=30)
    mp4 = make_video(root_b / "clip.mp4", source="testsrc", w=320, h=240, duration=30)

    conn = _conn(tmp_path)
    _, emit = _emitter()
    scan_id = scan_mod.run_scan(conn, [str(root_a), str(root_b)], emit)

    groups = queries.groups_json(conn, scan_id)
    assert len(groups) == 1
    keeper = [m for m in groups[0]["members"] if m["role"] == "keeper"][0]
    assert keeper["path"] == _norm(ts)
    assert _norm(mp4) != keeper["path"]
    conn.close()


def test_two_different_black_videos_are_not_grouped(tmp_path, make_video):
    root = tmp_path / "root"
    make_video(root / "b1.mp4", source="black", w=320, h=240, duration=30)
    make_video(root / "b2.mp4", source="black", w=640, h=480, duration=30)

    conn = _conn(tmp_path)
    _, emit = _emitter()
    scan_id = scan_mod.run_scan(conn, [str(root)], emit)

    assert queries.groups_json(conn, scan_id) == []
    conn.close()


def test_rescan_without_changes_uses_cache_and_never_probes(tmp_path, make_video, monkeypatch):
    root = tmp_path / "root"
    make_video(root / "a.mp4", source="testsrc", w=640, h=480, duration=30)
    make_video(root / "sub" / "b.mp4", source="testsrc", w=320, h=240, duration=30)
    make_video(root / "c.mp4", source="mandelbrot", w=320, h=240, duration=30)

    conn = _conn(tmp_path)
    _, emit = _emitter()
    first = scan_mod.run_scan(conn, [str(root)], emit)
    n_files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    assert n_files == 3

    def boom(*a, **k):
        raise AssertionError("probe/fingerprint must not be called for unchanged files")

    monkeypatch.setattr(probe_mod, "probe", boom)
    monkeypatch.setattr(scan_mod, "probe", boom)
    monkeypatch.setattr(scan_mod, "fingerprint", boom)

    second = scan_mod.run_scan(conn, [str(root)], emit)
    stats = json.loads(conn.execute("SELECT stats FROM scans WHERE id=?", (second,)).fetchone()[0])
    assert second != first
    assert stats["cached"] == n_files
    assert stats["unreadable"] == 0
    conn.close()


def test_text_file_renamed_mp4_is_unreadable_and_scan_completes(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    fake = root / "fake.mp4"
    fake.write_text("this is not a video at all")

    conn = _conn(tmp_path)
    events, emit = _emitter()
    scan_id = scan_mod.run_scan(conn, [str(root)], emit)

    row = _file_row(conn, fake)
    assert row["status"] == "unreadable"
    assert row["error"]
    scan_row = conn.execute("SELECT status FROM scans WHERE id=?", (scan_id,)).fetchone()
    assert scan_row["status"] == "done"
    assert any(e["type"] == "file_error" for e in events)
    assert any(e["type"] == "scan_done" for e in events)
    conn.close()


def test_thumbnail_written_for_normal_video(tmp_path, make_video):
    root = tmp_path / "root"
    make_video(root / "clip.mp4", source="testsrc", w=640, h=480, duration=30)
    thumbs = tmp_path / "thumbs"

    conn = _conn(tmp_path)
    _, emit = _emitter()
    scan_mod.run_scan(conn, [str(root)], emit, thumbs_dir=str(thumbs))

    row = conn.execute("SELECT id, thumb FROM files").fetchone()
    assert row["thumb"] == os.path.join(str(thumbs), f"{row['id']}.jpg")
    assert os.path.isfile(row["thumb"])
    from PIL import Image

    with Image.open(row["thumb"]) as img:
        assert img.width <= scan_mod.THUMB_WIDTH
    conn.close()


def test_missing_root_raises_before_writing_a_scan(tmp_path):
    conn = _conn(tmp_path)
    _, emit = _emitter()
    with pytest.raises(ValueError):
        scan_mod.run_scan(conn, [str(tmp_path / "nope")], emit)
    assert conn.execute("SELECT COUNT(*) FROM scans").fetchone()[0] == 0
    conn.close()
