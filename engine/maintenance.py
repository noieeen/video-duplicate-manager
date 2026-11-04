"""Cache maintenance: forget fingerprints, metadata and thumbnails without touching History or Undo.

Contract: docs/ENGINE_SPEC.md, section "Fresh scan, clear cache, files from folders".
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone

TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"  # same as engine.db.now()
STALE_SCAN_AFTER = timedelta(hours=6)


def _ensure_no_active_scan(conn: sqlite3.Connection) -> None:
    """Refuse while a scan marked 'running' started less than STALE_SCAN_AFTER ago."""
    now = datetime.now(timezone.utc)
    for row in conn.execute("SELECT started_at FROM scans WHERE status='running'"):
        started = datetime.strptime(row["started_at"], TS_FORMAT).replace(tzinfo=timezone.utc)
        if now - started < STALE_SCAN_AFTER:
            raise RuntimeError("cannot clear the cache while a scan is running")


def clear_cache(conn: sqlite3.Connection, thumbs_dir: str) -> dict:
    """Delete every fingerprint, reset ok/unreadable files to 'new' without metadata, and delete
    the `*.jpg` files directly inside thumbs_dir. Returns the counts of what was removed.

    Scans, groups, actions and moved/missing file rows are left alone.
    """
    _ensure_no_active_scan(conn)

    fp = conn.execute("DELETE FROM fingerprints").rowcount
    reset = conn.execute(
        """UPDATE files SET status='new', error=NULL, duration=NULL, width=NULL, height=NULL,
                   bit_rate=NULL, codec=NULL, score=NULL, thumb=NULL, probed_at=NULL
            WHERE status IN ('ok', 'unreadable')"""
    ).rowcount
    conn.commit()

    thumbs_deleted = 0
    if os.path.isdir(thumbs_dir):
        for name in os.listdir(thumbs_dir):
            path = os.path.join(thumbs_dir, name)
            if name.endswith(".jpg") and os.path.isfile(path):
                os.remove(path)
                thumbs_deleted += 1

    return {"fingerprints": fp, "files_reset": reset, "thumbs_deleted": thumbs_deleted}
