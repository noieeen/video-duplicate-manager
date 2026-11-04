import hashlib
import os

from engine import apply as apply_mod
from engine import db, queries
from engine import scan as scan_mod


def _emitter():
    events = []

    def emit(type, **data):
        events.append({"type": type, **data})

    return events, emit


def _norm(p):
    return os.path.realpath(p)


def _sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _label(root):
    return os.path.basename(_norm(root))


def _setup(tmp_path, make_video):
    """HD keeper in rootA/a/clip.mp4, SD duplicate in rootB/clip.mp4. Returns handles."""
    root_a, root_b = tmp_path / "rootA", tmp_path / "rootB"
    hd = make_video(root_a / "a" / "clip.mp4", source="testsrc", w=640, h=480, duration=30)
    sd = make_video(root_b / "clip.mp4", source="testsrc", w=320, h=240, duration=30)
    conn = db.connect(str(tmp_path / "lib" / "library.db"))
    _, emit = _emitter()
    scan_id = scan_mod.run_scan(conn, [str(root_a), str(root_b)], emit)
    group = queries.groups_json(conn, scan_id)[0]
    sd_id = conn.execute("SELECT id FROM files WHERE path=?", (_norm(sd),)).fetchone()[0]
    return conn, {
        "hd": hd, "sd": sd, "root_a": str(root_a), "root_b": str(root_b),
        "group_id": group["id"], "sd_id": sd_id, "dup_root": str(tmp_path / "dups"),
    }


def test_confirm_and_apply_moves_only_the_duplicate(tmp_path, make_video):
    conn, h = _setup(tmp_path, make_video)
    keeper_before = _sha(h["hd"])
    queries.set_group_status(conn, h["group_id"], "confirmed")
    _, emit = _emitter()

    batch = apply_mod.apply_groups(conn, None, h["dup_root"], emit)

    expected = os.path.join(h["dup_root"], _label(h["root_b"]), "clip.mp4")
    assert os.path.isfile(expected)
    assert not os.path.exists(h["sd"])
    assert os.path.isfile(h["hd"]) and _sha(h["hd"]) == keeper_before

    rows = conn.execute("SELECT op, ok, src, dst FROM actions WHERE batch_id=?", (batch,)).fetchall()
    assert len(rows) == 1
    assert rows[0]["op"] == "move" and rows[0]["ok"] == 1
    assert rows[0]["src"] == _norm(h["sd"])
    assert rows[0]["dst"] == os.path.abspath(expected)
    assert conn.execute("SELECT status FROM files WHERE id=?", (h["sd_id"],)).fetchone()[0] == "moved"
    assert queries.groups_json(conn, status="applied")[0]["id"] == h["group_id"]
    conn.close()


def test_collision_gets_suffix_and_existing_file_is_untouched(tmp_path, make_video):
    conn, h = _setup(tmp_path, make_video)
    sd_sha = _sha(h["sd"])
    existing = os.path.join(h["dup_root"], _label(h["root_b"]), "clip.mp4")
    os.makedirs(os.path.dirname(existing), exist_ok=True)
    with open(existing, "wb") as f:
        f.write(b"someone else's file, different content")
    existing_sha = _sha(existing)

    queries.set_group_status(conn, h["group_id"], "confirmed")
    _, emit = _emitter()
    apply_mod.apply_groups(conn, None, h["dup_root"], emit)

    assert _sha(existing) == existing_sha
    moved = os.path.join(h["dup_root"], _label(h["root_b"]), "clip_1.mp4")
    assert os.path.isfile(moved)
    assert _sha(moved) == sd_sha
    conn.close()


def test_pending_group_is_not_applied_when_no_ids_given(tmp_path, make_video):
    conn, h = _setup(tmp_path, make_video)
    _, emit = _emitter()

    batch = apply_mod.apply_groups(conn, None, h["dup_root"], emit)

    assert os.path.isfile(h["sd"])
    assert conn.execute("SELECT COUNT(*) FROM actions WHERE batch_id=?", (batch,)).fetchone()[0] == 0
    conn.close()


def test_ignored_member_is_not_moved(tmp_path, make_video):
    conn, h = _setup(tmp_path, make_video)
    queries.set_role(conn, h["group_id"], h["sd_id"], "ignored")
    queries.set_group_status(conn, h["group_id"], "confirmed")
    _, emit = _emitter()

    batch = apply_mod.apply_groups(conn, None, h["dup_root"], emit)

    assert os.path.isfile(h["sd"])
    assert conn.execute("SELECT COUNT(*) FROM actions WHERE batch_id=?", (batch,)).fetchone()[0] == 0
    conn.close()


def test_undo_restores_original_path_and_resets_group(tmp_path, make_video):
    conn, h = _setup(tmp_path, make_video)
    sd_sha = _sha(h["sd"])
    queries.set_group_status(conn, h["group_id"], "confirmed")
    _, emit = _emitter()
    batch = apply_mod.apply_groups(conn, None, h["dup_root"], emit)
    assert not os.path.exists(h["sd"])

    restored = apply_mod.undo_batch(conn, batch, emit)

    assert restored == 1
    assert os.path.isfile(h["sd"]) and _sha(h["sd"]) == sd_sha
    ops = conn.execute(
        "SELECT op, ok FROM actions WHERE batch_id=? ORDER BY id", (batch,)
    ).fetchall()
    assert [(r["op"], r["ok"]) for r in ops] == [("move", 1), ("restore", 1)]
    assert conn.execute("SELECT status FROM files WHERE id=?", (h["sd_id"],)).fetchone()[0] == "ok"
    assert conn.execute("SELECT status FROM dup_groups WHERE id=?", (h["group_id"],)).fetchone()[0] == "pending"

    # A second undo of the same batch is a no-op.
    assert apply_mod.undo_batch(conn, batch, emit) == 0
    conn.close()


def test_undo_never_overwrites_an_occupied_original_path(tmp_path, make_video):
    conn, h = _setup(tmp_path, make_video)
    queries.set_group_status(conn, h["group_id"], "confirmed")
    _, emit = _emitter()
    batch = apply_mod.apply_groups(conn, None, h["dup_root"], emit)
    with open(h["sd"], "wb") as f:  # someone recreated the original path
        f.write(b"new occupant")

    assert apply_mod.undo_batch(conn, batch, emit) == 0
    with open(h["sd"], "rb") as f:
        assert f.read() == b"new occupant"
    conn.close()


def test_moved_file_keeps_original_casing(tmp_path, make_video):
    root_a, root_b = tmp_path / "RootA", tmp_path / "RootB"
    make_video(root_a / "Show" / "Ep01.MP4", source="testsrc", w=640, h=480, duration=30)
    make_video(root_b / "Season 1" / "Ep01_Copy.MP4", source="testsrc", w=320, h=240, duration=30)
    conn = db.connect(str(tmp_path / "lib" / "library.db"))
    _, emit = _emitter()
    scan_mod.run_scan(conn, [str(root_a), str(root_b)], emit)
    queries.set_group_status(conn, queries.groups_json(conn)[0]["id"], "confirmed")
    apply_mod.apply_groups(conn, None, str(tmp_path / "dups"), emit)
    moved = [os.path.relpath(os.path.join(d, f), tmp_path / "dups")
             for d, _, fs in os.walk(tmp_path / "dups") for f in fs]
    assert moved == [os.path.join("RootB", "Season 1", "Ep01_Copy.MP4")]
    conn.close()
