# Lessons and gotchas

Things learned while building this repo (2026-11-05) that the code alone doesn't explain. Each entry gives the symptom, then the cause, then what we did or what to do.

## Media / ffmpeg

- **`.ts` files always scored 0.** ffprobe reports stream `bit_rate` as `N/A` for MPEG-TS, and `-of csv` prints the stream row twice, so `map(int, …)` raised and a bare `except` returned 0. → Make one `ffprobe -of json` call, fall back to `format.bit_rate`, and parse each field separately (`engine/probe.py`).
- **Seeking past EOF.** ffmpeg 7 exits non-zero and writes nothing, so a stale frame from an earlier file can't be reused. Verified; no guard needed.
- **Fast input seek returned no frame on some `.ts` files.** MPEG-TS has no seek index, and with sparse keyframes (here one keyframe at the start) `-ss` before `-i` finds nothing to decode. Moving `-ss` after `-i` works but decodes from the start, which is very slow at 90% of a 2-hour file. → Use the fast seek first, then the accurate seek only as a fallback (`fingerprint.extract_frame`).
- **Black/fade frames match everything.** Two different black clips give identical hashes (cosine 1.0 with CLIP). → Drop frames whose grayscale std-dev is < 8 (`is_uniform`) and require at least 2 shared non-uniform frames.
- **Short videos were never scanned.** The old code used fixed timestamps (5:00/2:00/10:00), which all land past EOF on short clips. → Sample at fractions of the duration instead.
- **Some `.ts` files are really headless fragmented MP4.** A scan of `D:\new-3` reported 25 of 378 `.ts` files as `unreadable` with `mov,mp4…: trun track id unknown, no tfhd was found … error reading header`. The bytes start straight with `moof`/`mdat` pairs; there is no `ftyp`/`moov` init segment. This is typical of an HLS/CMAF (fMP4) download that dropped the `#EXT-X-MAP` init segment and saved the media under a `.ts` name. Without `moov` there is no codec config (avcC/SPS/PPS) or track timescale, so neither ffmpeg nor players can decode them. **This is not an engine bug.** `unreadable` is correct, and the files can't be repaired without the original init segment. Possible improvement: detect "starts with `moof`" and show a clear reason ("fragmented MP4 without init segment") instead of the raw ffmpeg error. Check quickly by reading the first 8 bytes: bytes 4–8 = `moof` or `styp` means headless fMP4.
- **Combining the 3 frame grabs into one ffmpeg call** (`-ss t -i f` ×3 with 3 `-map … -f image2pipe -`) gave a stdout that couldn't be split back into images. Unresolved. If retried, write to 3 named outputs or use a filter graph with `select`, not one shared pipe.

## Detection quality (measured on the owner's library: 3,319 files)

- Pair distances (max over the 3 sample frames, same-length pairs): true matches 0–10, then a thin grey zone 12–22 (10 pairs), random pairs 24–50 (~120k pairs). pHash with a median threshold always sets exactly 32 bits, so distances are even.
- No random pair had **2 of 3** frames within 10. 13 pairs had 2 close frames and one far one (e.g. `{0: 0, 1: 0, 2: 24}`): a different sample frame, not a different video. → The rule changed from "all shared frames ≤ 10" to "≥ `min_frames` (2) shared frames ≤ 10". Result: 71 → 84 groups, 152 → 166 GB. `max_distance` stays 10.
- Trimmed copies (different length) are still not found: durations must agree within 1%. Finding them needs hashes every few seconds plus sequence alignment, which means decoding much more of every file. Parked; not worth the scan-time cost without a real case.
- Thousands of same-length files used to make the duration window O(n²) in Python. Multi-index hashing was considered, but with distance 10 its chunks are ~6 bits, so it yields *more* candidates than the duration window. → numpy `bitwise_count` over the whole window per file: 5,000 same-length files in 0.3 s, real library in 33 ms.

## Convert (measured: RTX 4070, driver 610.88, 1080p H.264 5 Mb/s .ts, 60 s segments, warm cache)

- NVENC re-encode is bound by the **single NVENC engine**, not by decode or CPU. Total throughput stays flat however it runs: preset p5 gives 11.7x realtime with 1 file, 12.0x with 2, 12.0x with 4. `-hwaccel cuda -hwaccel_output_format cuda` gives 11.7x, the same. → Parallel convert workers and GPU decode were **not** added; they would be complexity for ~3%.
- GPU decode alone (29x) is *slower* than CPU decode alone (53x) on this CPU.
- The real lever is the preset. At `-cq 23`, p1 runs at 28.9x (SSIM 0.9920, +9% size), p4 at 16.3x (SSIM 0.9914, +1% size), and p5 at 11.7x (SSIM 0.9922).
- Re-encoding this source at `-cq 23` made the file **1.6x bigger** (60 MB vs 38 MB per minute). For .ts → .mp4, Copy (remux) is the right default; re-encode only to fix broken streams or codecs.
- Copy mode is disk-bound: parallel only helps across different physical drives.
- Shrink on the same real clip: AV1 NVENC cq36 p5 → 63% of the source in 6.6 s per minute of video (SSIM 0.987); p1 → 57% in 3.5 s (SSIM 0.986). At the same `-cq`, p1 came out *smaller* than p5 (slightly lower quality), so "Fast" is not a size penalty for AV1 the way it is for H.264.
- The research agent's standard scale filter `scale='min(1920,iw)':'min(1080,ih)':force_original_aspect_ratio=decrease` "fits in 1920x1080", which shrinks a 1080x1920 portrait clip to 608x1080. The owner's 1080p cap means short side ≤ 1080 → choose `scale=-2:1080` or `scale=1080:-2` from the orientation.
- libx264 in this ffmpeg build silently drops `-color_trc`; to tag a test clip as HDR use `-x264-params colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc`.
- Keep-rule tests need a noisy source (testsrc2 + noise): clean synthetic clips compress so well that any encode passes the 85% rule, which hides bugs.

## Storage / paths

- **pHash overflowed SQLite.** The hash is unsigned 64-bit; SQLite INTEGER is signed. → `db.u64_to_db` / `db.u64_from_db` everywhere.
- **Moved files were renamed to lowercase.** Paths were stored via `os.path.normcase`, and the move destination was built from the stored path. → Store `os.path.realpath` (on Windows this returns the on-disk casing), and apply `normcase` only for comparisons.
- **`shutil.move` overwrites silently across drives** (copy2 + unlink). On the same drive Windows `os.rename` raises instead. → Pick a free name (`_1`, `_2`, …) and never overwrite.
- **Parallel scan:** a new file's `file_id` follows completion order, not walk order, so thumbnail filenames differ between `--workers 1` and `--workers 4`. Groups, keepers and stats are identical (tested).

## Performance (measured on D:\Downloads: 1,624 mp4, 3 TB, 16 TB SATA HDD)

- Per file, sequential: ffprobe about 200 ms plus 3 frame grabs at about 250 ms each, which comes to about 1.0 file/s (about 28 min for 1,624 files).
- Threads help because the work is subprocess/decode time (the GIL is released): 2 workers 1.8 files/s, **4 workers 2.6 files/s**, 8 workers 3.0 files/s. At 8, HDD seek contention shows (ffprobe median goes 206 → 816 ms). → Default 4 workers **per drive**, one pool per drive.
- A GPU doesn't help this pipeline: pHash costs under 1 ms of CPU, and NVDEC setup costs more than decoding one frame. Measured on an RTX 4070 with 1080p H.264 and a warm cache: one frame takes **CPU 247–321 ms vs `-hwaccel cuda` 606 ms**. Revisit only for semantic embeddings or heavy 4K HEVC decode, and measure first.
- An exact-duplicate fast path (size + head/tail hash, about 94 ms/file to read) isn't worth it for this library: only 3 same-size sets out of 1,624 files.
- **Full scan with `--workers 4`:** 1,624 files in **751 s (12.5 min)**, against about 28 min projected sequentially (about 2.2× faster; the app was also scanning the same HDD for part of that run). It found 0 unreadable files, **59 groups / 65 duplicates, 114.8 GB reclaimable**. In 58 of 59 groups the durations match within 1 s, which is strong evidence of true duplicates. One group differs by 45 s (8135 s vs 8180 s, within the 1% window), probably a re-cut; review it by eye. Filename similarity is often low (0.14–0.4), so the files were renamed and pHash catches what name matching would miss.
- Two scans reading the same HDD at once slow each other down badly. Don't benchmark while the app is scanning.

## Electron app

- **Thumbnails didn't load.** A privileged *standard* custom scheme requires a host, so `thumb:///C%3A…` fails to parse. → Use `thumb://local/<encoded path>`. Unit tests and typecheck missed this; only the live run caught it.
- Electron 32+ removed `File.path`. Get drag-drop paths with `webUtils.getPathForFile` (exposed as `pathForFile`).
- `dialog.showOpenDialog` with `openDirectory` + `multiSelections` works on Windows for picking several folders.
- Vite config is `vite.config.mts`, because the package is CJS (the built main uses `require`/`__dirname`).
- npm 12 blocks the `electron-winstaller` install script. That only matters for Squirrel builds, not NSIS.
- Main-process and preload changes need an app restart. **Before killing the app, check whether it is running an engine command** (look for `python.exe … engine` processes whose db path is under `AppData\Roaming`). Restarting mid-scan kills it.
- `<video>` from a custom scheme needs `stream: true` in `registerSchemesAsPrivileged`, and seeking needs real `Range` handling (206 + `Content-Range`). `serveMedia` streams with `fs.createReadStream({start, end})`.
- Live test without touching the owner's data: `electron . --remote-debugging-port=9333 --user-data-dir=<scratch>`, then drive `window.api` over CDP (`/json` → `Runtime.evaluate`, `Page.captureScreenshot`).

## Packaging / Docker

- PyInstaller bundled **torch** from the dev venv (scipy optionally imports it), which made the engine 488 MB instead of 143 MB. → `--exclude-module torch/torchvision/torchaudio`. Better still, recreate the venv without torch.
- `ffmpeg` on PATH here is a **scoop shim** (136 KB). `build_engine.py` follows the `.shim` file to the real binary before copying it.
- The bundled full ffmpeg build is 148 MB of the 254 MB installer. → CI now bundles the gyan.dev essentials build (still has libx264 + h264_nvenc; the job checks this).
- ImageHash pulled scipy (109 MB) + PyWavelets into the engine. → numpy pHash (`C[:8] @ X @ C[:8].T` with an unnormalised DCT-II matrix). Gotcha: on synthetic gradients scipy's DCT returns exact 0.0 coefficients, the matrix product ~3e-13, and the strict `> median` flipped up to 24 bits. → compare against `median + 1e-9·max|coef|`. Real frames: 1,500/1,500 identical. Frozen engine 143 → 56 MB.
- Docker: apt `ffmpeg` is about 457 MB of shared libs. → `COPY --from=mwader/static-ffmpeg:7.1.1 /ffmpeg /ffprobe /usr/local/bin/`. The image went from 1.12 GB to 871 MB, and from 2.19 GB with the old torch image. scipy (via ImageHash) is another 109 MB.
- `${VAR:?msg}` in docker-compose also breaks `docker compose build`.
- Git Bash rewrites `/videos` into a Windows path. → `MSYS_NO_PATHCONV=1 docker compose run …`.
- Docker Desktop isn't started automatically; it lives at `%LOCALAPPDATA%\Programs\DockerDesktop\Docker Desktop.exe`.
- GitHub Actions: Node 20 was deprecated → actions bumped to @v7, Node 24.
- Root `.gitignore` has a bare `build` line, which also ignored `app/build/` (electron-builder's icon folder). → `!app/build/`.
- electron-builder treats an empty `CSC_LINK` as "no certificate", so passing missing secrets is safe.

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
