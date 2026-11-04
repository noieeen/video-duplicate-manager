"""Read and small write helpers over the library database, used by the CLI."""
from __future__ import annotations

import sqlite3

GROUP_STATUSES = ("pending", "confirmed", "applied", "dismissed")
ROLES = ("keeper", "duplicate", "ignored")


def latest_scan_id(conn: sqlite3.Connection) -> int | None:
    """Newest scan that finished with status 'done' (cancelled/failed/running scans are skipped)."""
    row = conn.execute(
        "SELECT id FROM scans WHERE status='done' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return row["id"] if row else None


def groups_json(
    conn: sqlite3.Connection, scan_id: int | None = None, status: str | None = None
) -> list[dict]:
    """Groups of one scan (default: latest done scan), keeper first then by score desc."""
    if scan_id is None:
        scan_id = latest_scan_id(conn)
    if scan_id is None:
        return []

    sql = "SELECT id, scan_id, status FROM dup_groups WHERE scan_id=?"
    params: list = [scan_id]
    if status is not None:
        sql += " AND status=?"
        params.append(status)
    sql += " ORDER BY id"

    groups: dict[int, dict] = {}
    for r in conn.execute(sql, params):
        groups[r["id"]] = {"id": r["id"], "scan_id": r["scan_id"], "status": r["status"], "members": []}
    if not groups:
        return []

    rows = conn.execute(
        """SELECT m.group_id, m.file_id, m.role, f.path, f.size, f.duration, f.width, f.height,
                  f.bit_rate, f.codec, f.score, f.thumb, f.status
             FROM group_members m
             JOIN files f ON f.id = m.file_id
             JOIN dup_groups g ON g.id = m.group_id
            WHERE g.scan_id = ?""",
        (scan_id,),
    )
    for r in rows:
        group = groups.get(r["group_id"])
        if group is None:
            continue
        group["members"].append({
            "file_id": r["file_id"],
            "path": r["path"],
            "role": r["role"],
            "size": r["size"],
            "duration": r["duration"],
            "width": r["width"],
            "height": r["height"],
            "bit_rate": r["bit_rate"],
            "codec": r["codec"],
            "score": r["score"],
            "thumb": r["thumb"],
            "status": r["status"],
        })

    for group in groups.values():
        group["members"].sort(key=lambda m: (m["role"] != "keeper", -(m["score"] or 0), m["path"]))
    return list(groups.values())


def history_json(conn: sqlite3.Connection) -> list[dict]:
    """One entry per batch, newest first. 'failed' counts moves with ok=0 or an unfinished ok NULL."""
    rows = conn.execute(
        """SELECT batch_id, MIN(at) AS at,
                  SUM(CASE WHEN op='move' AND ok=1 THEN 1 ELSE 0 END) AS moved,
                  SUM(CASE WHEN op='restore' AND ok=1 THEN 1 ELSE 0 END) AS restored,
                  SUM(CASE WHEN ok IS NULL OR ok=0 THEN 1 ELSE 0 END) AS failed
             FROM actions
            GROUP BY batch_id
            ORDER BY MAX(id) DESC"""
    ).fetchall()
    return [
        {"batch_id": r["batch_id"], "at": r["at"], "moved": r["moved"],
         "restored": r["restored"], "failed": r["failed"]}
        for r in rows
    ]


def set_role(conn: sqlite3.Connection, group_id: int, file_id: int, role: str) -> None:
    """Set a member's role. Making a file keeper demotes the group's previous keeper to duplicate."""
    if role not in ROLES:
        raise ValueError(f"invalid role: {role}")
    row = conn.execute(
        "SELECT 1 FROM group_members WHERE group_id=? AND file_id=?", (group_id, file_id)
    ).fetchone()
    if row is None:
        raise ValueError(f"file {file_id} is not in group {group_id}")
    if role == "keeper":
        conn.execute(
            "UPDATE group_members SET role='duplicate' "
            "WHERE group_id=? AND role='keeper' AND file_id<>?",
            (group_id, file_id),
        )
    conn.execute(
        "UPDATE group_members SET role=? WHERE group_id=? AND file_id=?",
        (role, group_id, file_id),
    )
    conn.commit()


def set_group_status(conn: sqlite3.Connection, group_id: int, status: str) -> None:
    if status not in GROUP_STATUSES:
        raise ValueError(f"invalid group status: {status}")
    cur = conn.execute("UPDATE dup_groups SET status=? WHERE id=?", (status, group_id))
    if cur.rowcount == 0:
        raise ValueError(f"no group {group_id}")
    conn.commit()


def confirm_groups(
    conn: sqlite3.Connection, group_ids: list[int] | None, scan_id: int | None
) -> int:
    """Mark pending groups confirmed. group_ids=None means every pending group of scan_id.

    Only groups currently 'pending' change. Returns how many were confirmed.
    """
    if group_ids is None:
        if scan_id is None:
            return 0
        cur = conn.execute(
            "UPDATE dup_groups SET status='confirmed' WHERE status='pending' AND scan_id=?",
            (scan_id,),
        )
        conn.commit()
        return cur.rowcount
    n = 0
    for gid in group_ids:
        cur = conn.execute(
            "UPDATE dup_groups SET status='confirmed' WHERE status='pending' AND id=?", (gid,)
        )
        n += cur.rowcount
    conn.commit()
    return n
