"""SQLite library database: connection and schema migrations."""
import os
import sqlite3
from datetime import datetime, timezone

MIGRATIONS: list[str] = [
    """
CREATE TABLE files(
  id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE, root TEXT NOT NULL,
  size INTEGER NOT NULL, mtime REAL NOT NULL,
  duration REAL, width INTEGER, height INTEGER, bit_rate INTEGER, codec TEXT,
  score REAL, thumb TEXT,
  status TEXT NOT NULL DEFAULT 'new',   -- new|ok|unreadable|moved|missing
  error TEXT, probed_at TEXT);
CREATE TABLE fingerprints(
  file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  method TEXT NOT NULL, frame_idx INTEGER NOT NULL, value INTEGER NOT NULL,
  PRIMARY KEY(file_id, method, frame_idx));
CREATE TABLE scans(
  id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
  roots TEXT NOT NULL, settings TEXT NOT NULL,
  status TEXT NOT NULL,                  -- running|done|cancelled|failed
  stats TEXT);
CREATE TABLE dup_groups(
  id INTEGER PRIMARY KEY, scan_id INTEGER NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
  method TEXT NOT NULL, created_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending'); -- pending|confirmed|applied|dismissed
CREATE TABLE group_members(
  group_id INTEGER NOT NULL REFERENCES dup_groups(id) ON DELETE CASCADE,
  file_id INTEGER NOT NULL REFERENCES files(id),
  role TEXT NOT NULL,                    -- keeper|duplicate|ignored
  PRIMARY KEY(group_id, file_id));
CREATE TABLE actions(
  id INTEGER PRIMARY KEY, batch_id TEXT NOT NULL, file_id INTEGER NOT NULL REFERENCES files(id),
  op TEXT NOT NULL,                      -- move|restore
  src TEXT NOT NULL, dst TEXT NOT NULL, at TEXT NOT NULL,
  ok INTEGER, error TEXT);               -- ok NULL = started, not finished (crash marker)
CREATE INDEX idx_actions_batch ON actions(batch_id);
""",
]


def u64_to_db(v: int) -> int:
    """SQLite INTEGER is signed 64-bit; store an unsigned 64-bit hash as its two's-complement."""
    return v - (1 << 64) if v >= (1 << 63) else v


def u64_from_db(v: int) -> int:
    return v & 0xFFFFFFFFFFFFFFFF


def now() -> str:
    """UTC ISO-8601 timestamp, seconds precision."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def migrate(conn: sqlite3.Connection) -> None:
    """Apply pending migrations; user_version counts applied ones."""
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for i in range(current, len(MIGRATIONS)):
        conn.executescript(MIGRATIONS[i])
        conn.execute(f"PRAGMA user_version = {i + 1}")


def connect(path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    migrate(conn)
    return conn
