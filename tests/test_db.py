import sqlite3

from engine import db

TABLES = {"files", "fingerprints", "scans", "dup_groups", "group_members", "actions"}


def _user_version(conn):
    return conn.execute("PRAGMA user_version").fetchone()[0]


def test_connect_creates_schema(tmp_path):
    conn = db.connect(str(tmp_path / "sub" / "lib.db"))
    names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert TABLES <= names
    assert _user_version(conn) == len(db.MIGRATIONS)
    assert conn.row_factory is sqlite3.Row
    conn.close()


def test_reconnect_is_fine(tmp_path):
    path = str(tmp_path / "lib.db")
    db.connect(path).close()
    conn = db.connect(path)
    assert _user_version(conn) == len(db.MIGRATIONS)
    conn.close()


def test_foreign_key_cascade(tmp_path):
    conn = db.connect(str(tmp_path / "lib.db"))
    conn.execute(
        "INSERT INTO files(path, root, size, mtime) VALUES ('/a.mp4', '/', 1, 0)"
    )
    fid = conn.execute("SELECT id FROM files").fetchone()["id"]
    conn.execute(
        "INSERT INTO fingerprints(file_id, method, frame_idx, value) VALUES (?, 'phash', 0, 123)",
        (fid,),
    )
    conn.execute("DELETE FROM files WHERE id = ?", (fid,))
    count = conn.execute("SELECT COUNT(*) FROM fingerprints").fetchone()[0]
    assert count == 0
    conn.close()


def test_u64_roundtrip(tmp_path):
    from engine import db
    conn = db.connect(str(tmp_path / "x.db"))
    for v in (0, 1, (1 << 63) - 1, 1 << 63, (1 << 64) - 1):
        conn.execute("DELETE FROM files")
        conn.execute("INSERT INTO files(id, path, root, size, mtime) VALUES (1, 'p', 'r', 1, 1)")
        conn.execute("INSERT INTO fingerprints VALUES (1, 'phash', 0, ?)", (db.u64_to_db(v),))
        got = conn.execute("SELECT value FROM fingerprints").fetchone()[0]
        assert db.u64_from_db(got) == v
