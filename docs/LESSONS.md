# Lessons and gotchas

Things learned while building this repo (2026-11-05) that the code alone doesn't explain. Each entry gives the symptom, then the cause, then what we did or what to do.

## Media / ffmpeg

- **`.ts` files always scored 0.** ffprobe reports stream `bit_rate` as `N/A` for MPEG-TS, and `-of csv` prints the stream row twice, so `map(int, …)` raised and a bare `except` returned 0. → Make one `ffprobe -of json` call, fall back to `format.bit_rate`, and parse each field separately (`engine/probe.py`).
- **Seeking past EOF.** ffmpeg 7 exits non-zero and writes nothing, so a stale frame from an earlier file can't be reused. Verified; no guard needed.
- **Fast input seek returned no frame on some `.ts` files.** MPEG-TS has no seek index, and with sparse keyframes (here one keyframe at the start) `-ss` before `-i` finds nothing to decode. Moving `-ss` after `-i` works but decodes from the start, which is very slow at 90% of a 2-hour file. → Use the fast seek first, then the accurate seek only as a fallback (`fingerprint.extract_frame`).
- **Black/fade frames match everything.** Two different black clips give identical hashes (cosine 1.0 with CLIP). → Drop frames whose grayscale std-dev is < 8 (`is_uniform`) and require at least 2 shared non-uniform frames.
- **Short videos were never scanned.** The old code used fixed timestamps (5:00/2:00/10:00), which all land past EOF on short clips. → Sample at fractions of the duration instead.
- **Combining the 3 frame grabs into one ffmpeg call** (`-ss t -i f` ×3 with 3 `-map … -f image2pipe -`) gave a stdout that couldn't be split back into images. Unresolved. If retried, write to 3 named outputs or use a filter graph with `select`, not one shared pipe.

## Storage / paths

- **pHash overflowed SQLite.** The hash is unsigned 64-bit; SQLite INTEGER is signed. → `db.u64_to_db` / `db.u64_from_db` everywhere.
- **Moved files were renamed to lowercase.** Paths were stored via `os.path.normcase`, and the move destination was built from the stored path. → Store `os.path.realpath` (on Windows this returns the on-disk casing), and apply `normcase` only for comparisons.
- **`shutil.move` overwrites silently across drives** (copy2 + unlink). On the same drive Windows `os.rename` raises instead. → Pick a free name (`_1`, `_2`, …) and never overwrite.
- **Parallel scan:** a new file's `file_id` follows completion order, not walk order, so thumbnail filenames differ between `--workers 1` and `--workers 4`. Groups, keepers and stats are identical (tested).

## Performance (measured on D:\Downloads: 1,624 mp4, 3 TB, 16 TB SATA HDD)

- Per file, sequential: ffprobe about 200 ms plus 3 frame grabs at about 250 ms each, which comes to about 1.0 file/s (about 28 min for 1,624 files).
- Threads help because the work is subprocess/decode time (the GIL is released): 2 workers 1.8 files/s, **4 workers 2.6 files/s**, 8 workers 3.0 files/s. At 8, HDD seek contention shows (ffprobe median goes 206 → 816 ms). → Default 4 workers **per drive**, one pool per drive.
- A GPU doesn't help this pipeline: pHash costs under 1 ms of CPU, and NVDEC setup costs more than decoding one frame. Revisit only for semantic embeddings or heavy 4K HEVC decode, and measure first.
- An exact-duplicate fast path (size + head/tail hash, about 94 ms/file to read) isn't worth it for this library: only 3 same-size sets out of 1,624 files.
- Two scans reading the same HDD at once slow each other down badly. Don't benchmark while the app is scanning.

## Electron app

- **Thumbnails didn't load.** A privileged *standard* custom scheme requires a host, so `thumb:///C%3A…` fails to parse. → Use `thumb://local/<encoded path>`. Unit tests and typecheck missed this; only the live run caught it.
- Electron 32+ removed `File.path`. Get drag-drop paths with `webUtils.getPathForFile` (exposed as `pathForFile`).
- `dialog.showOpenDialog` with `openDirectory` + `multiSelections` works on Windows for picking several folders.
- Vite config is `vite.config.mts`, because the package is CJS (the built main uses `require`/`__dirname`).
- npm 12 blocks the `electron-winstaller` install script. That only matters for Squirrel builds, not NSIS.
- Main-process and preload changes need an app restart. **Before killing the app, check whether it is running an engine command** (look for `python.exe … engine` processes whose db path is under `AppData\Roaming`). Restarting mid-scan kills it.
- Live test without touching the owner's data: `electron . --remote-debugging-port=9333 --user-data-dir=<scratch>`, then drive `window.api` over CDP (`/json` → `Runtime.evaluate`, `Page.captureScreenshot`).

## Packaging / Docker

- PyInstaller bundled **torch** from the dev venv (scipy optionally imports it), which made the engine 488 MB instead of 143 MB. → `--exclude-module torch/torchvision/torchaudio`. Better still, recreate the venv without torch.
- `ffmpeg` on PATH here is a **scoop shim** (136 KB). `build_engine.py` follows the `.shim` file to the real binary before copying it.
- The bundled full ffmpeg build is 148 MB of the 254 MB installer. A slimmer build is the cheapest size win.
- Docker: apt `ffmpeg` is about 457 MB of shared libs. → `COPY --from=mwader/static-ffmpeg:7.1.1 /ffmpeg /ffprobe /usr/local/bin/`. The image went from 1.12 GB to 871 MB, and from 2.19 GB with the old torch image. scipy (via ImageHash) is another 109 MB.
- `${VAR:?msg}` in docker-compose also breaks `docker compose build`.
- Git Bash rewrites `/videos` into a Windows path. → `MSYS_NO_PATHCONV=1 docker compose run …`.
- Docker Desktop isn't started automatically; it lives at `%LOCALAPPDATA%\Programs\DockerDesktop\Docker Desktop.exe`.
- GitHub Actions warns that Node 20 is deprecated for `actions/*@v4/v5`. Bump them when convenient.

## Repo / tooling

- Many tracked files have **CRLF** line endings. A Python `str.replace` on a multi-line LF pattern silently matches nothing. → Use the Edit tool, or check that the change actually happened.
- `sed -i 'N,Md'` deleted the wrong range once (off by one), and the result was a collection error. Re-read the lines after deleting.
- Both a branch and a tag are named `stable`. Use `refs/tags/stable`.
- The legacy `main.py` hard-coded the owner's real drives and moved files on run. Never run old snapshots. A read-only `engine scan` on real folders is safe (it writes only the DB and thumbnails).

## Working with Haiku workers (haiku-army)

- **One written contract** (`docs/ENGINE_SPEC.md`) let 3 workers build engine core, fingerprinting and the Electron app in parallel without conflicts. Fix the spec *before* briefing: the CLI table didn't say where `--db` goes, and one worker had to flag the mismatch.
- Haiku tasks took about 1–8 min and 85k–160k tokens each. Briefs that listed exact files, out-of-scope files, invariants, and the tests to write came back passing on the first round.
- A good worker behaviour: it found a bug in a frozen module, marked the test `xfail(strict=True)`, and reported it instead of editing the module. Its proposed fix (`-ss` after `-i`) was slow on long files. Verify the root cause before accepting a worker's fix.
- Workers notice working-tree changes made by the orchestrator or other workers (staged deletions, edits to other files) and report them as surprises. Say in the brief that the orchestrator may change other files in parallel.
- Unit tests and typecheck passed while the thumbnail URL was broken. **Always do a live end-to-end run** of the real app before calling it done.
