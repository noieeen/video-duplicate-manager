# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A video duplicate finder made of two parts:

- a headless Python **engine** (`engine/`) that owns all logic and the SQLite library;
- an **Electron app** (`app/`, Vite + React + TypeScript) that only spawns the engine and renders its events.

Docker runs the same engine headless. **`docs/ENGINE_SPEC.md` is the contract** between all parts (module APIs, schema, CLI, events, `window.api`). Update it whenever you change an interface. `docs/REVIEW.md` records why the legacy PyQt/CLIP version (tag `stable`) was replaced.

## Engine data flow

`scan` → `probe` (one `ffprobe -of json`) → `fingerprint` (pHash at 10/50/90% of duration; uniform frames dropped) → `grouping.find_groups` (duration window + all shared frames within Hamming distance, then union-find) → `scoring.pick_keeper` → `dup_groups`/`group_members` rows. `scan` never moves files. `apply` moves only `duplicate` members of `confirmed` groups. Before each move it writes an `actions` row and commits it, and it never overwrites. `undo` reverses a `batch_id`.

Non-obvious points:

- pHash values are unsigned 64-bit but SQLite is signed. Always use `db.u64_to_db` / `db.u64_from_db`.
- Paths are stored via `os.path.realpath`, which keeps the on-disk casing so moved files keep their names. Case-insensitive comparisons use `normcase` on top of that.
- A re-scan reuses rows whose `(size, mtime)` are unchanged (`cached` in stats). It doesn't re-probe them.
- `extract_frame` tries a fast input seek first, then falls back to an accurate output seek. MPEG-TS with sparse keyframes needs the fallback.
- CLI global options go **before** the subcommand: `python -m engine --db X [--human] scan --root A`.
- The app's dev mode spawns `<repo>/.venv/Scripts/python.exe -m engine`. Packaged mode spawns `resources/engine/engine.exe` plus `resources/ffmpeg/`. The thumbnail protocol `thumb://` only serves files under `userData/thumbs`.

## Commands

```bash
.venv/Scripts/python -m pytest -q                      # all tests (generate synthetic videos via ffmpeg)
.venv/Scripts/python -m pytest -q tests/test_apply.py  # one file
.venv/Scripts/python -m pytest -q -k collision         # one test
cd app && npm run typecheck && npm run build           # app checks
cd app && npm run dev                                  # run the app against the dev engine
.\scripts\build_all.ps1                                # engine (PyInstaller) + ffmpeg + NSIS installer
docker compose run --rm engine scan --root /videos     # headless, VIDEO_DIR mounted at /videos
```

Never point a test or manual run at the owner's real video folders (`D:\...`). Use `tmp_path` or a scratch folder.

## Branches

`main` is the working branch (the GitHub default). The `stable` tag/branch and `master` hold the legacy PyQt/CLIP version: `master` = stable + a broken parallel attempt. Since both a branch and a tag are named `stable`, use `refs/tags/stable` in git commands.
