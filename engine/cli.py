"""Command-line entry point: python -m engine --db PATH [--human] <command> [options].

Every result is an event on stdout (see Emitter). Exit 0 on success, 1 on error (after emitting
`error {message}`). Contract: docs/ENGINE_SPEC.md, section "CLI".
"""
from __future__ import annotations

import argparse
import os
import sys

from engine import db
from engine.apply import apply_groups, undo_batch
from engine.convert import ENCODERS, MODES, SPEEDS, ok_files_with_ext, run_convert, video_files_under
from engine.events import Emitter
from engine.library import GROUP_BYS, library_files, library_json
from engine.maintenance import clear_cache
from engine.queries import (
    GROUP_STATUSES,
    ROLES,
    confirm_groups,
    groups_json,
    history_json,
    latest_scan_id,
    set_group_status,
    set_role,
)
from engine.scan import DEFAULT_SETTINGS, run_scan


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m engine", description="Video duplicate engine.")
    p.add_argument("--db", required=True, help="path to the SQLite library")
    p.add_argument("--human", action="store_true", help="readable lines instead of JSON")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("scan", help="scan folders for duplicate videos (never moves files)")
    s.add_argument("--root", action="append", required=True, metavar="DIR")
    s.add_argument("--thumbs", metavar="DIR", help="thumbnail folder (default: <db dir>/thumbs)")
    s.add_argument("--max-distance", type=int, default=DEFAULT_SETTINGS["max_distance"])
    s.add_argument("--min-frames", type=int, default=DEFAULT_SETTINGS["min_frames"])
    s.add_argument("--workers", type=int, default=DEFAULT_SETTINGS["workers"],
                   help="files analysed in parallel per drive (default: %(default)s)")
    s.add_argument("--fresh", action="store_true",
                   help="ignore the scan cache: re-read and re-fingerprint every file")
    s.add_argument("--exclude", action="append", default=None, metavar="PATTERN",
                   help="skip paths matching PATTERN (repeatable)")

    g = sub.add_parser("groups", help="list duplicate groups of a scan")
    g.add_argument("--scan", type=int, help="scan id (default: latest done scan)")
    g.add_argument("--status", choices=GROUP_STATUSES)

    r = sub.add_parser("set-role", help="set a member's role in a group")
    r.add_argument("--group", type=int, required=True)
    r.add_argument("--file", type=int, required=True)
    r.add_argument("--role", choices=ROLES, required=True)

    c = sub.add_parser("confirm", help="confirm pending groups")
    which = c.add_mutually_exclusive_group(required=True)
    which.add_argument("--group", type=int, action="append")
    which.add_argument("--all-pending", action="store_true",
                       help="confirm every pending group of the latest scan")

    d = sub.add_parser("dismiss", help="dismiss a group")
    d.add_argument("--group", type=int, required=True)

    a = sub.add_parser("apply", help="move duplicates of confirmed groups into a folder, or to the Recycle Bin")
    a.add_argument("--dup-root", metavar="DIR", help="folder to move duplicates into (required unless --trash)")
    a.add_argument("--trash", action="store_true",
                   help="send duplicates to the Recycle Bin instead of moving them (ignores --dup-root)")
    a.add_argument("--group", type=int, action="append",
                   help="group id (repeatable; default: all confirmed groups)")

    u = sub.add_parser("undo", help="restore the files moved by one apply batch")
    u.add_argument("--batch", required=True)

    sub.add_parser("history", help="list apply batches")

    lb = sub.add_parser("library", help="disk space of the library, grouped by potential, resolution, codec or folder")
    lb.add_argument("--root", action="append", metavar="DIR",
                    help="only files under this folder (repeatable)")
    lb.add_argument("--group-by", choices=GROUP_BYS, default="potential")
    lb.add_argument("--group", metavar="KEY", help="list the files of this group (largest first) instead")
    lb.add_argument("--limit", type=int, default=200, help="files per page with --group (default: %(default)s)")
    lb.add_argument("--offset", type=int, default=0, help="files to skip with --group (default: %(default)s)")

    cc = sub.add_parser("clear-cache", help="forget fingerprints, metadata and thumbnails (keeps History/Undo)")
    cc.add_argument("--thumbs", metavar="DIR", help="thumbnail folder (default: <db dir>/thumbs)")

    f = sub.add_parser("files", help="list video files: of the latest done scan (--ext), or under folders (--root)")
    f.add_argument("--ext", metavar="EXT", help="e.g. .ts (case-insensitive)")
    f.add_argument("--root", action="append", metavar="DIR",
                   help="folder to walk recursively (repeatable); lists files from disk, not the DB")

    cv = sub.add_parser("convert", help="convert videos to MP4 (never overwrites or deletes the source)")
    cv.add_argument("--input", action="append", metavar="FILE", help="video file (repeatable)")
    cv.add_argument("--from-scan-ext", metavar="EXT",
                    help="add every ok file of the latest done scan with this extension")
    cv.add_argument("--mode", choices=MODES, default="copy",
                    help="copy = lossless remux (default); reencode = h264 + aac")
    cv.add_argument("--encoder", choices=ENCODERS, default="auto",
                    help="re-encode only: auto (GPU if available), nvenc, x264 (default: %(default)s)")
    cv.add_argument("--speed", choices=SPEEDS, default="quality",
                    help="re-encode only: quality (default) or fast (~2.5x faster with nvenc, ~10%% larger)")
    cv.add_argument("--out-dir", metavar="DIR", help="output folder (default: next to each source)")
    return p


def _dispatch(args: argparse.Namespace, conn, emit) -> None:
    cmd = args.command
    if cmd == "scan":
        thumbs = args.thumbs or os.path.join(os.path.dirname(os.path.abspath(args.db)), "thumbs")
        settings = {"max_distance": args.max_distance, "min_frames": args.min_frames,
                    "workers": args.workers, "fresh": args.fresh,
                    "exclude": list(args.exclude or [])}
        run_scan(conn, args.root, emit, settings=settings, thumbs_dir=thumbs)
    elif cmd == "clear-cache":
        thumbs = args.thumbs or os.path.join(os.path.dirname(os.path.abspath(args.db)), "thumbs")
        emit("cache_cleared", **clear_cache(conn, thumbs))
    elif cmd == "groups":
        scan_id = args.scan if args.scan is not None else latest_scan_id(conn)
        groups = groups_json(conn, scan_id, args.status) if scan_id is not None else []
        emit("groups", scan_id=scan_id, groups=groups)
    elif cmd == "set-role":
        set_role(conn, args.group, args.file, args.role)
        emit("ok")
    elif cmd == "confirm":
        if args.all_pending:
            n = confirm_groups(conn, None, latest_scan_id(conn))
        else:
            n = confirm_groups(conn, args.group, None)
        emit("ok", confirmed=n)
    elif cmd == "dismiss":
        set_group_status(conn, args.group, "dismissed")
        emit("ok")
    elif cmd == "apply":
        apply_groups(conn, args.group, args.dup_root, emit, trash=args.trash)
    elif cmd == "undo":
        undo_batch(conn, args.batch, emit)
    elif cmd == "history":
        emit("history", batches=history_json(conn))
    elif cmd == "library":
        if args.group is None:
            emit("library", **library_json(conn, args.root, args.group_by))
        else:
            emit("library_files", **library_files(conn, args.root, args.group_by, args.group,
                                                  args.limit, args.offset))
    elif cmd == "files":
        if args.root:
            emit("files", paths=video_files_under(args.root, args.ext))
        else:
            emit("files", paths=ok_files_with_ext(conn, args.ext))
    elif cmd == "convert":
        inputs = list(args.input or [])
        if args.from_scan_ext:
            inputs += ok_files_with_ext(conn, args.from_scan_ext)
        if not inputs:
            raise ValueError("no input files: pass --input FILE or --from-scan-ext EXT")
        run_convert(conn, inputs, emit, mode=args.mode, encoder=args.encoder, out_dir=args.out_dir,
                    speed=args.speed)
    else:  # unreachable: argparse enforces the choices
        raise ValueError(f"unknown command: {cmd}")


def _force_utf8_stdout() -> None:
    # Paths may contain Thai characters; the app reads stdout as UTF-8 regardless of console code page.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "files" and not args.root and args.ext is None:
        parser.error("the following arguments are required: --ext")
    if args.command == "apply" and not args.trash and not args.dup_root:
        parser.error("the following arguments are required: --dup-root (or --trash)")
    _force_utf8_stdout()
    emit = Emitter(human=args.human).emit
    conn = None
    try:
        conn = db.connect(args.db)
        _dispatch(args, conn, emit)
        return 0
    except KeyboardInterrupt:
        emit("error", message="cancelled")
        return 130
    except Exception as e:
        emit("error", message=str(e) or type(e).__name__)
        return 1
    finally:
        if conn is not None:
            conn.close()
