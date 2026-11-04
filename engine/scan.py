"""Scan roots for video files, probe and fingerprint them, and store duplicate groups.

Never moves or deletes video files. Contract: docs/ENGINE_SPEC.md, section "Scan".
`emit` is a callable `emit(type, **data)` (the CLI passes `Emitter(...).emit`).
"""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Callable

from PIL import Image

from engine import db
from engine.fingerprint import fingerprint
from engine.grouping import find_groups
from engine.probe import probe
from engine.scoring import pick_keeper, score

VIDEO_EXTS = {".mp4", ".ts", ".mkv", ".avi", ".mov", ".m4v", ".wmv", ".webm", ".flv"}
METHOD = "phash"
DEFAULT_SETTINGS = {"max_distance": 10, "min_frames": 2, "duration_tol": 1.0, "method": METHOD}
THUMB_WIDTH = 320
COMMIT_EVERY = 50

Emit = Callable[..., None]


def _norm(path: str) -> str:
    # realpath keeps the on-disk casing on Windows (so moved files keep their names);
    # comparisons that must be case-insensitive use os.path.normcase on top of this.
    return os.path.realpath(path)


def _normalise_roots(roots: list[str]) -> list[str]:
    """Normalise, drop duplicates and roots nested inside another root. Parents sort first."""
    out: list[str] = []
    seen: set[str] = set()
    for r in sorted({_norm(r) for r in roots}, key=os.path.normcase):
        key = os.path.normcase(r)
        if key in seen or any(key.startswith(os.path.normcase(o).rstrip(os.sep) + os.sep) for o in out):
            continue
        seen.add(key)
        out.append(r)
    return out


def _walk(roots: list[str]) -> list[tuple[str, str]]:
    """(normalised path, root it was found under) for every video file, sorted by path."""
    found: list[tuple[str, str]] = []
    for root in roots:
        for dirpath, _dirs, names in os.walk(root):
            for name in names:
                if os.path.splitext(name)[1].lower() in VIDEO_EXTS:
                    found.append((os.path.join(dirpath, name), root))
    found.sort()
    return found


def _has_fingerprints(conn: sqlite3.Connection, file_id: int) -> bool:
    return conn.execute(
        "SELECT 1 FROM fingerprints WHERE file_id=? AND method=? LIMIT 1", (file_id, METHOD)
    ).fetchone() is not None


def _save_thumb(fps, file_id: int, thumbs_dir: str) -> str | None:
    """Save the middle frame (index 1, else any non-None) as a JPEG at most THUMB_WIDTH wide."""
    chosen = None
    for idx, _h, img in fps:
        if idx == 1 and img is not None:
            chosen = img
    if chosen is None:
        for _idx, _h, img in fps:
            if img is not None:
                chosen = img
                break
    if chosen is None:
        return None

    img = chosen.convert("RGB")
    if img.width > THUMB_WIDTH:
        height = max(1, round(img.height * THUMB_WIDTH / img.width))
        img = img.resize((THUMB_WIDTH, height), Image.Resampling.LANCZOS)
    os.makedirs(thumbs_dir, exist_ok=True)
    out = os.path.join(thumbs_dir, f"{file_id}.jpg")
    img.save(out, "JPEG", quality=85)
    return out


def _process_file(conn, path: str, root: str, thumbs_dir: str | None, stats: dict, emit: Emit) -> None:
    st = os.stat(path)
    row = conn.execute(
        "SELECT id, size, mtime, status FROM files WHERE path=?", (path,)
    ).fetchone()
    if (
        row is not None
        and row["status"] == "ok"
        and row["size"] == st.st_size
        and row["mtime"] == st.st_mtime
        and _has_fingerprints(conn, row["id"])
    ):
        conn.execute("UPDATE files SET root=? WHERE id=?", (root, row["id"]))
        stats["cached"] += 1
        return

    conn.execute(
        """INSERT INTO files(path, root, size, mtime, status, error) VALUES(?, ?, ?, ?, 'new', NULL)
           ON CONFLICT(path) DO UPDATE SET root=excluded.root, size=excluded.size,
               mtime=excluded.mtime, status='new', error=NULL""",
        (path, root, st.st_size, st.st_mtime),
    )
    file_id = conn.execute("SELECT id FROM files WHERE path=?", (path,)).fetchone()["id"]
    try:
        meta = probe(path)
        fps = fingerprint(path, meta["duration"])
        thumb = _save_thumb(fps, file_id, thumbs_dir) if thumbs_dir else None
        conn.execute("DELETE FROM fingerprints WHERE file_id=? AND method=?", (file_id, METHOD))
        conn.executemany(
            "INSERT INTO fingerprints(file_id, method, frame_idx, value) VALUES(?, ?, ?, ?)",
            [(file_id, METHOD, idx, db.u64_to_db(h)) for idx, h, _img in fps if h is not None],
        )
        conn.execute(
            """UPDATE files SET duration=?, width=?, height=?, bit_rate=?, codec=?, score=?,
                   thumb=?, status='ok', error=NULL, probed_at=? WHERE id=?""",
            (meta["duration"], meta["width"], meta["height"], meta["bit_rate"], meta["codec"],
             score(meta), thumb, db.now(), file_id),
        )
    except Exception as e:  # any per-file failure marks the file and the scan goes on
        msg = str(e) or type(e).__name__
        conn.execute("DELETE FROM fingerprints WHERE file_id=? AND method=?", (file_id, METHOD))
        conn.execute(
            "UPDATE files SET status='unreadable', error=?, thumb=NULL, probed_at=? WHERE id=?",
            (msg, db.now(), file_id),
        )
        stats["unreadable"] += 1
        emit("file_error", path=path, error=msg)


def _index_files(conn, roots: list[str], thumbs_dir: str | None, emit: Emit) -> tuple[dict, set[str]]:
    files = _walk(roots)
    emit("files_found", count=len(files))
    stats = {"files": len(files), "cached": 0, "unreadable": 0}
    seen: set[str] = set()
    total = len(files)
    for i, (path, root) in enumerate(files, 1):
        seen.add(path)
        try:
            _process_file(conn, path, root, thumbs_dir, stats, emit)
        except OSError as e:  # file vanished or became unreadable between walk and stat
            stats["unreadable"] += 1
            emit("file_error", path=path, error=str(e) or type(e).__name__)
        if i % COMMIT_EVERY == 0:
            conn.commit()
        emit("progress", phase="fingerprint", done=i, total=total, path=path)
    conn.commit()
    return stats, seen


def _mark_missing(conn, roots: list[str], seen: set[str]) -> None:
    ph = ",".join("?" * len(roots))
    rows = conn.execute(
        f"SELECT id, path FROM files WHERE status='ok' AND root IN ({ph})", roots
    ).fetchall()
    gone = [(r["id"],) for r in rows if r["path"] not in seen]
    conn.executemany("UPDATE files SET status='missing' WHERE id=?", gone)
    conn.commit()


def _build_groups(conn, scan_id: int, roots: list[str], cfg: dict, emit: Emit) -> tuple[int, int]:
    ph = ",".join("?" * len(roots))
    file_rows = conn.execute(
        f"SELECT id, duration, path, size, score FROM files WHERE status='ok' AND root IN ({ph})",
        roots,
    ).fetchall()
    hash_rows = conn.execute(
        f"""SELECT fp.file_id, fp.frame_idx, fp.value
              FROM fingerprints fp JOIN files f ON f.id = fp.file_id
             WHERE fp.method=? AND f.status='ok' AND f.root IN ({ph})""",
        [METHOD, *roots],
    ).fetchall()

    hashes: dict[int, dict[int, int]] = {}
    for r in hash_rows:
        hashes.setdefault(r["file_id"], {})[r["frame_idx"]] = db.u64_from_db(r["value"])
    info = {r["id"]: r for r in file_rows}
    items = [
        (r["id"], float(r["duration"] or 0), hashes[r["id"]])
        for r in file_rows
        if r["id"] in hashes
    ]

    groups = find_groups(
        items,
        max_distance=cfg["max_distance"],
        min_frames=cfg["min_frames"],
        duration_tol=cfg["duration_tol"],
    )

    duplicates = 0
    for file_ids in groups:
        members = [
            {"score": float(info[fid]["score"] or 0), "size": info[fid]["size"], "path": info[fid]["path"]}
            for fid in file_ids
        ]
        keeper = pick_keeper(members)
        cur = conn.execute(
            "INSERT INTO dup_groups(scan_id, method, created_at, status) VALUES(?, ?, ?, 'pending')",
            (scan_id, METHOD, db.now()),
        )
        group_id = cur.lastrowid
        for i, fid in enumerate(file_ids):
            role = "keeper" if i == keeper else "duplicate"
            conn.execute(
                "INSERT INTO group_members(group_id, file_id, role) VALUES(?, ?, ?)",
                (group_id, fid, role),
            )
        duplicates += len(file_ids) - 1
        emit("group", group_id=group_id, file_ids=file_ids)
    conn.commit()
    return len(groups), duplicates


def _finish(conn, scan_id: int, status: str, stats: dict | None = None) -> None:
    conn.execute(
        "UPDATE scans SET status=?, finished_at=?, stats=? WHERE id=?",
        (status, db.now(), json.dumps(stats) if stats is not None else None, scan_id),
    )
    conn.commit()


def run_scan(
    conn: sqlite3.Connection,
    roots: list[str],
    emit: Emit,
    settings: dict | None = None,
    thumbs_dir: str | None = None,
) -> int:
    """Scan roots and store the result. Returns the scan id.

    Raises ValueError (before any row is written) when a root is not a directory.
    """
    cfg = {**DEFAULT_SETTINGS, **(settings or {})}
    for r in roots:
        if not os.path.isdir(r):
            raise ValueError(f"root is not a directory: {r}")
    norm_roots = _normalise_roots(roots)

    cur = conn.execute(
        "INSERT INTO scans(started_at, roots, settings, status) VALUES(?, ?, ?, 'running')",
        (db.now(), json.dumps(norm_roots), json.dumps(cfg)),
    )
    scan_id = cur.lastrowid
    conn.commit()
    emit("scan_started", scan_id=scan_id, roots=norm_roots)

    try:
        stats, seen = _index_files(conn, norm_roots, thumbs_dir, emit)
        _mark_missing(conn, norm_roots, seen)
        groups, duplicates = _build_groups(conn, scan_id, norm_roots, cfg, emit)
        stats.update(groups=groups, duplicates=duplicates)
        _finish(conn, scan_id, "done", stats)
    except KeyboardInterrupt:
        _finish(conn, scan_id, "cancelled")
        raise
    except Exception:
        _finish(conn, scan_id, "failed")
        raise

    emit("scan_done", scan_id=scan_id, **stats)
    return scan_id
