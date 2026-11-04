"""Move confirmed duplicates into a duplicates folder, and undo a batch of moves.

Contract: docs/ENGINE_SPEC.md, section "Apply / undo". Every move is logged in `actions`
(committed before the file moves) so each batch can be reversed. `emit` is `emit(type, **data)`.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import uuid
from typing import Callable

from engine.db import now

Emit = Callable[..., None]


def _root_label(root: str) -> str:
    base = os.path.basename(root.rstrip("\\/"))
    if base:
        return base
    # Root is a drive or filesystem root: use the drive letter ("C" for "c:\\").
    return os.path.splitdrive(root)[0].rstrip(":\\/") or "root"


def _free_dst(dup_root: str, root: str, src: str) -> str:
    """dup_root/<root label>/<path relative to root>, with _1, _2 ... before the extension
    until the name is free. Never returns an existing path."""
    rel = os.path.relpath(src, root)
    dst = os.path.join(dup_root, _root_label(root), rel)
    stem, ext = os.path.splitext(dst)
    candidate, n = dst, 1
    while os.path.exists(candidate):
        candidate = f"{stem}_{n}{ext}"
        n += 1
    return candidate


def _move_one(conn: sqlite3.Connection, batch_id: str, dup: sqlite3.Row, dup_root: str, emit: Emit) -> bool:
    src = dup["path"]
    dst = _free_dst(dup_root, dup["root"], src)
    cur = conn.execute(
        "INSERT INTO actions(batch_id, file_id, op, src, dst, at, ok, error) "
        "VALUES(?, ?, 'move', ?, ?, ?, NULL, NULL)",
        (batch_id, dup["file_id"], src, dst, now()),
    )
    action_id = cur.lastrowid
    conn.commit()  # the intent is on record before the file moves

    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(src, dst)
    except Exception as e:
        msg = str(e) or type(e).__name__
        conn.execute("UPDATE actions SET ok=0, error=? WHERE id=?", (msg, action_id))
        conn.commit()
        emit("file_error", path=src, error=msg)
        return False

    conn.execute("UPDATE actions SET ok=1 WHERE id=?", (action_id,))
    conn.execute("UPDATE files SET status='moved' WHERE id=?", (dup["file_id"],))
    conn.commit()
    return True


def apply_groups(
    conn: sqlite3.Connection,
    group_ids: list[int] | None,
    dup_root: str,
    emit: Emit,
) -> str:
    """Move the duplicate members of the given groups (None = all confirmed groups).

    Only groups in 'pending' or 'confirmed' are processed. A group becomes 'applied' when it
    has duplicate members and every one of them moved in this batch; otherwise it keeps its status
    so it can be retried. Returns the batch id.
    """
    batch_id = uuid.uuid4().hex
    dup_root = os.path.abspath(dup_root)

    if group_ids is None:
        groups = conn.execute(
            "SELECT id, status FROM dup_groups WHERE status='confirmed' ORDER BY id"
        ).fetchall()
    else:
        groups = []
        for gid in dict.fromkeys(group_ids):
            row = conn.execute("SELECT id, status FROM dup_groups WHERE id=?", (gid,)).fetchone()
            if row is None:
                emit("skip", group_id=gid, reason="not found")
            else:
                groups.append(row)

    targets = []
    for g in groups:
        if g["status"] not in ("pending", "confirmed"):
            emit("skip", group_id=g["id"], reason=f"status {g['status']}")
            continue
        dups = conn.execute(
            """SELECT m.file_id, f.path, f.root, f.status
                 FROM group_members m JOIN files f ON f.id = m.file_id
                WHERE m.group_id = ? AND m.role = 'duplicate'
                ORDER BY m.file_id""",
            (g["id"],),
        ).fetchall()
        targets.append((g["id"], dups))

    total = sum(len(dups) for _, dups in targets)
    done = moved = failed = 0
    for group_id, dups in targets:
        group_ok = bool(dups)
        for d in dups:
            done += 1
            if d["status"] != "ok":
                emit("skip", group_id=group_id, file_id=d["file_id"], path=d["path"],
                     reason=f"file status {d['status']}")
                group_ok = False
            elif _move_one(conn, batch_id, d, dup_root, emit):
                moved += 1
            else:
                failed += 1
                group_ok = False
            emit("progress", phase="apply", done=done, total=total, path=d["path"])
        if group_ok:
            conn.execute("UPDATE dup_groups SET status='applied' WHERE id=?", (group_id,))
            conn.commit()

    emit("apply_done", batch_id=batch_id, moved=moved, failed=failed)
    return batch_id


def undo_batch(conn: sqlite3.Connection, batch_id: str, emit: Emit) -> int:
    """Move every successfully moved file of the batch back to its original path, newest first.

    Never overwrites: if the original path is occupied the file is left where it is and counted
    as failed. Returns the number restored. Moves already restored are skipped, so undo is repeatable.
    """
    rows = conn.execute(
        """SELECT a.file_id, a.src, a.dst FROM actions a
            WHERE a.batch_id = ? AND a.op = 'move' AND a.ok = 1
              AND NOT EXISTS (
                    SELECT 1 FROM actions r
                     WHERE r.op = 'restore' AND r.ok = 1 AND r.batch_id = a.batch_id
                       AND r.src = a.dst)
            ORDER BY a.id DESC""",
        (batch_id,),
    ).fetchall()

    restored = failed = 0
    for r in rows:
        src, dst = r["src"], r["dst"]  # restore goes dst -> src
        if os.path.exists(src):
            failed += 1
            emit("file_error", path=dst, error=f"original path already exists: {src}")
            continue
        try:
            os.makedirs(os.path.dirname(src), exist_ok=True)
            shutil.move(dst, src)
        except Exception as e:
            failed += 1
            emit("file_error", path=dst, error=str(e) or type(e).__name__)
            continue

        conn.execute(
            "INSERT INTO actions(batch_id, file_id, op, src, dst, at, ok, error) "
            "VALUES(?, ?, 'restore', ?, ?, ?, 1, NULL)",
            (batch_id, r["file_id"], dst, src, now()),
        )
        conn.execute("UPDATE files SET status='ok' WHERE id=?", (r["file_id"],))
        conn.execute(
            "UPDATE dup_groups SET status='pending' WHERE status != 'dismissed' "
            "AND id IN (SELECT group_id FROM group_members WHERE file_id=?)",
            (r["file_id"],),
        )
        conn.commit()
        restored += 1

    emit("undo_done", batch_id=batch_id, restored=restored, failed=failed)
    return restored
