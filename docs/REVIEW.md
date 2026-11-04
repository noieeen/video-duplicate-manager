# Review: `refs/tags/stable` (2026-11-05)

Outsider review (/scrutinize, Fable) plus checks run in this session against synthetic test videos, generated with ffmpeg (no real drives touched). **RAN** = reproduced by experiment, **READ** = from reading the code only.

## Branch comparison

| Ref | Verdict |
|---|---|
| `stable` (`a725f4e`) | The best of the three. CLI (`main.py`) + PyQt5 GUI (`ui.py`) + PyInstaller build. |
| `master` (+2 commits) | A regression. Stop crashes (`ProcessingWorker.stop()` deleted). An empty threshold field blocks Start. `ui-v2.py` raises on `if path and emb` (numpy truthiness) and reloads CLIP in each `Pool` worker. |
| `feature/ui` | Branched off `init`. CLI only; `ui.py` and `build.bat` are empty. |

## Run results

- The CLI pipeline (local venv and Docker) is correct on the HD/SD pair + an unrelated clip: it keeps 640x480, moves 320x240, and leaves the unrelated file. (RAN)
- The GUI window starts. Its worker moves nothing unless `temp_data/` already exists in the cwd, and it still shows 100%. (RAN)

## Findings (most severe first)

### BLOCKER 1: the GUI silently skips every video
`ui.py:39` writes frames to `temp_data/`, which nothing creates. ffmpeg fails, `extract_frame` swallows the error, embeddings come back `{}`, and progress still reaches 100%. **RAN.**
Fix: `os.makedirs(frame_dir, exist_ok=True)` in the worker, using a real temp dir.

### BLOCKER 2: black/static frames make unrelated videos "duplicates"
Every file is sampled at the same fixed timestamps (5:00, 2:00, 10:00), and `find_duplicates` decides on a single cosine > 0.95. Two different black clips scored cosine 1.000 and were grouped together. **RAN.**
Fix: sample at 10%/50%/90% of duration, skip near-uniform frames, and require every sample to match. Add a dry-run report.

### MAJOR 3: every `.ts` file scores 0, so the wrong copy is kept
`get_video_score` runs `map(int, …)` over ffprobe CSV output. For MPEG-TS the output is `320,240,N/A` (printed twice), which raises, then `except: return 0`. An `.mp4` therefore always beats a `.ts`, whatever the resolution. **RAN.**
Fix: one `ffprobe -of json` call, use `format.bit_rate`, and fall back per field.

### MAJOR 4: moves overwrite existing files
The destination is `duplicate_dir/<basename>`. A cross-drive `shutil.move` (copy2 + unlink) onto an existing name replaces that file, so it is lost. **RAN.**
Fix: keep the relative path or add a numeric suffix, and never overwrite.

### MAJOR 5: import-time side effects
Importing `main.py` loads CLIP and runs `os.makedirs(r"D:\vid-frame")`. On a machine without `D:` the exe dies before its window appears, with no message (`--noconsole`). In Docker it creates a literal `D:\vid-frame` dir under `/app`. **READ.**

### MAJOR 6: worker errors and completion are invisible
Exceptions are `print`ed, and stdout goes nowhere under `--noconsole`. There is no finished signal, and Start can be clicked twice, which gives two threads moving the same files. **READ.**

### MINOR 7: O(n²) pairwise torch calls
n=2000 took 75 s. That extrapolates to about 31 min at 10k and about 2 h at 20k. Stop can't interrupt this phase. **RAN.**
Fix: a normalized matrix `E @ E.T`, or a duration prefilter.

### MINOR 8: videos under 2 min are never scanned
All three seek points fall past EOF, and `extract_frame` returns `None` silently. **RAN.**

### Nits
- `ui.py` redefines `extract_frame` (shadowing main's), and `CREATE_NO_WINDOW` is Windows-only.
- Source dirs spelled two ways (`D:/x` vs `D:\x`) make the same file appear twice with sim 1.0, and it gets moved.
- `requirements.txt` pins PyPI `openai-clip==1.0.1`, which can lack `clip.load`. The Dockerfile installs CLIP from git (unpinned).
- In Docker, the `*.ts` glob is case-sensitive.
- A stale-frame reuse risk was suspected and ruled out: ffmpeg 7 exits non-zero and writes nothing when the seek is past EOF. (RAN)

## Simpler alternatives considered

1. Stage, don't move: a dry-run report by default, with moves only on `--apply`.
2. Perceptual hashes (pHash, already a dependency via `ImageHash`) of several frames instead of one CLIP embedding. This drops torch (about 1 GB) and is immune to semantic/black-frame collisions.
3. A duration prefilter, comparing only within ±1 s buckets.
4. An existing tool: czkawka (video dedup, free).

**Verdict: rework.** The GUI finds nothing. When the CLI does run, a single-frame match plus a broken quality score decides which file leaves the library, and nothing records what moved.
