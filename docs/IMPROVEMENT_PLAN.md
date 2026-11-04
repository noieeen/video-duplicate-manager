# Improvement plan

Status: **implemented 2026-11-05** (Phases 0–4); see README.md and docs/ENGINE_SPEC.md. Decisions taken: pHash, React, per-group confirm + "Accept all suggestions".
Base: branch `main` (created from `refs/tags/stable`). Findings referenced as R1–R8 are in [REVIEW.md](REVIEW.md).

## Decisions taken

- **`main` is the working branch.** `master` is kept untouched as an archive of the ui-v2/parallel attempt. `feature/ui` is obsolete.
- **The GUI becomes an Electron app.** The PyQt5 GUI is retired completely: `ui.py`, `build.bat`, `build.sh`, `VideoDuplicateManager.spec`, and the PyQt5 deps in `requirements.txt`.
- **Add a small database:** SQLite, through Python's stdlib `sqlite3`. There is no server to run, it is a single file, and it adds no dependency.

## Target architecture

```
Electron app (TypeScript)                Python engine (headless)
┌───────────────────────────┐  spawn    ┌────────────────────────────────┐
│ main process              │ ────────► │ engine scan|apply|undo|status  │
│  - spawns engine, reads   │ ◄──────── │  JSON-lines events on stdout    │
│    JSON-lines events      │  stdout   │  (progress, group, error, done) │
│ renderer (React + Vite)   │           │                                 │
│  - folders, scan progress │           │ ffmpeg/ffprobe ─► fingerprint   │
│  - group review, thumbs   │           │ grouping ─► scoring             │
│  - apply / undo           │           │ SQLite (sole writer, WAL)       │
└───────────────────────────┘           └────────────────────────────────┘
                                         same engine runs in Docker (CLI)
```

- **The engine is the only owner of logic and of the DB.** Electron never touches video files or SQLite directly. It sends commands and renders events, which keeps one code path for the GUI, the CLI and Docker.
- **IPC is stdio JSON-lines rather than an HTTP server:** no port, no firewall prompt, and the process lifetime is tied to the app. Commands arrive as argv, events go out one JSON object per line, and Stop = kill the child (the engine must stay safe to kill at any point, which the DB transactions guarantee).
- **Packaging:** the engine is built with PyInstaller `--onedir`, then shipped with ffmpeg/ffprobe binaries as electron-builder `extraResources`. The DB lives in Electron `app.getPath('userData')/library.db`, and that path is passed to the engine.

## Database (SQLite)

Purpose: incremental re-scans (skip unchanged files), a persistent review state, and a durable move log that makes **undo** possible.

```sql
PRAGMA journal_mode=WAL;  -- schema version in PRAGMA user_version

files(id PK, path TEXT UNIQUE, size INT, mtime REAL,
      duration REAL, width INT, height INT, bit_rate INT, codec TEXT,
      status TEXT,            -- ok | unreadable | too_short | missing
      probed_at TEXT)
fingerprints(file_id FK, method TEXT, frame_idx INT, value BLOB,
      PRIMARY KEY(file_id, method, frame_idx))   -- pHash or CLIP vector
scans(id PK, started_at, finished_at, roots JSON, settings JSON, status TEXT)
dup_groups(id PK, scan_id FK, method TEXT, created_at)
group_members(group_id FK, file_id FK, score REAL, similarity REAL,
      role TEXT)              -- keeper | duplicate | ignored (user override)
actions(id PK, batch_id TEXT, file_id FK, op TEXT,   -- move | restore
      src TEXT, dst TEXT, at TEXT, ok INT, error TEXT)
```

Rules:
- A file is re-probed or re-fingerprinted only if `(size, mtime)` changed. This alone makes re-runs on TB libraries take minutes instead of hours.
- Every move writes an `actions` row **before** the move, then marks `ok` after it. Undo replays a `batch_id` in reverse. A crash mid-batch is detectable (`ok IS NULL`).
- Migrations are a list of SQL scripts keyed by `user_version`, with no ORM.

## Phases

### Phase 0: engine extraction + safety (no GUI work yet)
Goal: a headless `engine/` package that is safe to run on real drives.
1. Move the pipeline from `main.py` into `engine/` (probe, fingerprint, group, score, apply), removing all import-time side effects (R5). Paths, roots and settings come only from CLI args / the DB.
2. Add the SQLite layer + schema + migrations, as above.
3. Make `scan` report-only. Moves happen only via `apply --batch` (a dry run by default, which directly fixes the absent safety net).
4. Make moves collision-safe: preserve the relative path under the duplicates root, never overwrite (R4), and log every move in `actions`, with `undo`.
5. Fix scoring: one `ffprobe -of json`, `format.bit_rate` fallback, per-field defaults (R3).
6. Add the JSON-lines event protocol + a `--human` text mode for the terminal.
7. Add a pytest suite on synthetic ffmpeg videos: an HD/SD pair, a `.ts`/`.mp4` pair, two black clips, a same-basename pair, a clip under 2 min, and undo round-trip.
8. Point Docker at `engine` (replacing `docker/run.py`), with `apply` as an explicit second command.

Done when: the tests pass, and `engine scan` on a real folder moves nothing and produces groups visible via `engine status`.

### Phase 1: detection quality
9. Sample frames at 10%/50%/90% of duration, skip near-uniform frames, require all samples to match (R2, R8).
10. **Decision needed: pHash vs CLIP.** Recommended: pHash (`ImageHash`). It targets "same video, re-encoded", is immune to black-frame/genre collisions, and drops torch, which removes about 1 GB from the Electron installer. Keep `method` in the DB so CLIP can be added back as an option.
11. Group in a single vectorised pass (Hamming distance matrix for pHash, `E @ E.T` for CLIP) with a duration-bucket prefilter (R7).

### Phase 2: Electron app MVP (replaces PyQt)
12. Scaffold `app/` with electron + Vite + React + TypeScript, plus electron-builder.
13. Screens: **Folders** (add/remove/drag-drop roots, duplicates root), **Scan** (progress per phase, a cancel that kills the engine), **Review** (groups with thumbnails from extracted frames, keeper suggested by score, override per file, open in Explorer/player), **Apply** (summary → confirm → progress), **History** (batches with Undo).
14. Delete `ui.py`, `build.bat`, `build.sh`, `VideoDuplicateManager.spec` and the PyQt5/pyinstaller-ui deps. Rewrite README/CLAUDE.md.

### Phase 3: packaging
15. PyInstaller `--onedir` engine + ffmpeg/ffprobe as `extraResources`, and electron-builder NSIS installer for Windows.
16. A GitHub Actions build on `main` producing the installer artifact.

### Phase 4: hygiene
17. Trim `requirements.txt` to direct engine deps, pinned. Set up the Node lockfile for `app/`.
18. Branches: decide whether to delete `feature/ui`, the `stable` branch (the tag stays as a marker) and eventually `master`.

## Open decisions for the owner

- pHash vs CLIP (step 10). Recommended: pHash.
- React vs plain TS for the renderer (step 12). Recommended: React. The Review screen has a lot of state.
- Whether the "keeper" auto-choice may be applied without per-group review (a bulk "accept all suggestions"), or every group must be confirmed.
