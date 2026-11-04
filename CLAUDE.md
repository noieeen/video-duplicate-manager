# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Windows desktop tool that finds near-duplicate videos (`.mp4`, `.ts`) across folders and **moves** every copy except the "best" one into a duplicates folder. It has two entry points:

- `main.py` is the CLI and the backend. Run directly, it scans the hard-coded `SOURCE_DIRS` (real `D:\` drives) and moves files. **Never run `python main.py` as a test.** Use a throwaway folder instead (see Docker / `docker/run.py`).
- `ui.py` is the PyQt5 GUI. The user picks source folders and a duplicate folder, and the GUI calls `main`'s functions on a worker thread.

There are no tests, no linter and no package structure.

## Branches

`main` is the working branch (created from the `stable` tag). Both a branch and a tag are named `stable`, so use `refs/tags/stable` or `refs/heads/stable` in git commands. The planned direction (Python engine + SQLite + Electron app, with the PyQt GUI retired) is in `docs/IMPROVEMENT_PLAN.md`, and the known bugs are in `docs/REVIEW.md`.

- `master` = stable + a "parallel" attempt (`ui-v2.py`, `ui.py` edits). It is known broken: `ProcessingWorker.stop()` was removed, and `if path and emb` on a numpy array raises.
- `feature/ui` branched off `init` and is CLI-only (its `ui.py` and `build.bat` are empty).

## Architecture (stable)

The pipeline lives in `main.py`:

1. `process_videos`: recursive glob, then `extract_frame` (ffmpeg `-ss` at 5:00, then 2:00, then 10:00, with a raw 1920x1080 grab as last resort, 10s timeout each), then a CLIP `ViT-B/32` image embedding.
2. `find_duplicates`: a greedy O(n²) pairwise cosine check with `> threshold` (default 0.95). The first-seen video anchors its group.
3. `process_duplicates`: an `ffprobe` score (width × height × duration × bitrate) picks the keeper. The rest go `shutil.move` → `duplicate_dir/<basename>` (flat).

Non-obvious coupling:

- **Importing `main` has side effects.** It loads the CLIP model (and downloads it to `~/.cache/clip` on first run) and creates `FRAME_DIR`/`DUPLICATE_DIR` on `D:\`. `ui.py` imports `model`, `preprocess` and `device` from it.
- `process_videos` reads the module global `FRAME_DIR` at call time, so callers can redirect it by assigning `main.FRAME_DIR` (this is how `docker/run.py` works).
- `ui.py` reimplements the frame/embedding loop itself, writing frames to the relative `temp_data/`, so that it can report progress and stop. It also redefines `extract_frame` with `CREATE_NO_WINDOW`, which shadows the imported one. It uses `find_duplicates`/`process_duplicates` from `main` with `keep_best=True` and threshold 0.95 hard-coded.

## Commands

Windows, from the repo root. Requires `ffmpeg`/`ffprobe` on PATH.

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python ui.py          # GUI
build.bat             # PyInstaller one-file exe -> dist\VideoDuplicateManager.exe
```

- `build.bat` bundles `.venv\Lib\site-packages\clip\bpe_simple_vocab_16e6.txt.gz` (CLIP's tokenizer vocab). The exe fails without it. `VideoDuplicateManager.spec` is the generated equivalent.
- CLIP trap: the PyPI `openai-clip` can give `module 'clip' has no attribute 'load'`. The fix is `pip uninstall openai-clip && pip install git+https://github.com/openai/CLIP.git`.
- Docker (headless CLI only, CPU torch, no GUI): `$env:VIDEO_DIR="D:/folder"; docker compose run --rm dedup`. The container mounts `VIDEO_DIR` → `/videos` and `OUTPUT_DIR` (default `./output`) → `/output/{frames,duplicates}`, and caches CLIP in the `clip-cache` volume. `docker compose build` also needs `VIDEO_DIR` set (any value).
- Safe local run on a test folder without touching `D:\`: `PYTHONPATH=. SOURCE_DIRS="<dir1>;<dir2>" FRAME_DIR=<tmp> DUPLICATE_DIR=<tmp> .venv/Scripts/python docker/run.py`.
- The current `.venv` has CPU torch. For CUDA: `pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118`.
