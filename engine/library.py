"""Disk-space breakdown of the library: group the files by potential, resolution, codec or folder.

Only rows with status 'ok' or 'unreadable' count (moved, missing, trashed and new rows are left out).
One bucketing function (_bucketed) serves both the summary and the per-group file list.
"""
from __future__ import annotations

import os
import sqlite3

GROUP_BYS = ("potential", "resolution", "codec", "folder")
TOP_N = 5
EFFICIENT_CODECS = ("hevc", "av1", "vp9")
HIGH_BITRATE = 4_000_000  # bits per second

POTENTIAL_ORDER = ("above_1080p", "old_high", "old_low", "efficient", "unreadable")
RESOLUTION_ORDER = ("sd", "720p", "1080p", "1440p", "2160p", "unknown", "unreadable")


def _potential(r: sqlite3.Row) -> tuple[str, str]:
    if r["status"] == "unreadable":
        return "unreadable", "Unreadable"
    w, h = r["width"], r["height"]
    if w and h and min(w, h) > 1080:
        return "above_1080p", "Above 1080p"
    if (r["codec"] or "").lower() in EFFICIENT_CODECS:
        return "efficient", "Already HEVC/AV1/VP9"
    if (r["bit_rate"] or 0) > HIGH_BITRATE:
        return "old_high", "H.264/older, over 4 Mb/s"
    return "old_low", "H.264/older, 4 Mb/s or less"


def _resolution(r: sqlite3.Row) -> tuple[str, str]:
    if r["status"] == "unreadable":
        return "unreadable", "Unreadable"
    w, h = r["width"], r["height"]
    if not w or not h:
        return "unknown", "Unknown"
    short = min(w, h)  # portrait 1080x1920 counts as 1080p
    if short <= 480:
        return "sd", "480p or less"
    if short <= 720:
        return "720p", "720p"
    if short <= 1080:
        return "1080p", "1080p"
    if short <= 1440:
        return "1440p", "1440p"
    return "2160p", "4K and above"


def _codec_key(r: sqlite3.Row) -> str:
    return (r["codec"] or "").lower() or "unknown"


def _top_keys(pairs: list[tuple[sqlite3.Row, str]]) -> list[str]:
    """The TOP_N raw keys with the most bytes (ties broken by key name, so the order is stable)."""
    totals: dict[str, int] = {}
    for r, key in pairs:
        totals[key] = totals.get(key, 0) + r["size"]
    return sorted(totals, key=lambda k: (-totals[k], k))[:TOP_N]


def _classify(rows: list[sqlite3.Row], group_by: str) -> tuple[list[tuple], list[str]]:
    """Return ([(row, key, label)], keys in display order)."""
    if group_by == "potential":
        return [(r, *_potential(r)) for r in rows], list(POTENTIAL_ORDER)

    if group_by == "resolution":
        return [(r, *_resolution(r)) for r in rows], list(RESOLUTION_ORDER)

    if group_by == "codec":
        top = _top_keys([(r, _codec_key(r)) for r in rows if r["status"] != "unreadable"])
        labelled = []
        for r in rows:
            if r["status"] == "unreadable":
                labelled.append((r, "unreadable", "Unreadable"))
                continue
            key = _codec_key(r)
            if key in top:
                labelled.append((r, key, key.upper()))
            else:
                labelled.append((r, "other", "Other"))
        return labelled, [*top, "other", "unreadable"]

    # folder: the scan root is the key; unreadable files stay in their folder.
    top = _top_keys([(r, r["root"]) for r in rows])
    labelled = [(r, r["root"], r["root"]) if r["root"] in top else (r, "other", "Other") for r in rows]
    return labelled, [*top, "other"]


def _root_matchers(roots: list[str]) -> list[tuple[str, str]]:
    """(bare root, root + sep) per root, normcase'd and realpath'd, for prefix matching."""
    out = []
    for root in roots:
        bare = os.path.normcase(os.path.realpath(root)).rstrip(os.sep)
        out.append((bare, bare + os.sep))
    return out


def _load(conn: sqlite3.Connection, roots: list[str] | None) -> list[sqlite3.Row]:
    rows = conn.execute(
        "SELECT id, path, root, size, width, height, bit_rate, codec, duration, status "
        "FROM files WHERE status IN ('ok', 'unreadable')"
    ).fetchall()
    if not roots:
        return rows
    matchers = _root_matchers(roots)
    kept = []
    for r in rows:
        p = os.path.normcase(r["path"])
        if any(p == bare or p.startswith(prefix) for bare, prefix in matchers):
            kept.append(r)
    return kept


def _bucketed(conn: sqlite3.Connection, roots, group_by: str) -> tuple[list[tuple], list[str]]:
    if group_by not in GROUP_BYS:
        raise ValueError(f"unknown group_by: {group_by}")
    return _classify(_load(conn, roots), group_by)


def library_json(conn: sqlite3.Connection, roots, group_by: str) -> dict:
    labelled, order = _bucketed(conn, roots, group_by)
    groups: dict[str, dict] = {}
    for r, key, label in labelled:
        g = groups.setdefault(key, {"key": key, "label": label, "files": 0, "bytes": 0})
        g["files"] += 1
        g["bytes"] += r["size"]
    return {
        "group_by": group_by,
        "total_files": len(labelled),
        "total_bytes": sum(r["size"] for r, _, _ in labelled),
        "groups": [groups[k] for k in order if k in groups],
    }


def library_files(conn: sqlite3.Connection, roots, group_by: str, key: str,
                  limit: int = 200, offset: int = 0) -> dict:
    labelled, _ = _bucketed(conn, roots, group_by)
    members = sorted((r for r, k, _ in labelled if k == key), key=lambda r: (-r["size"], r["path"]))
    return {
        "group_by": group_by,
        "key": key,
        "total": len(members),
        "files": [
            {"file_id": r["id"], "path": r["path"], "size": r["size"], "width": r["width"],
             "height": r["height"], "codec": r["codec"], "bit_rate": r["bit_rate"],
             "duration": r["duration"], "status": r["status"]}
            for r in members[offset:offset + limit]
        ],
    }
