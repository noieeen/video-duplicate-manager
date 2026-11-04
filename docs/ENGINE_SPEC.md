# Engine + app contract (source of truth for implementers)

Python 3.12, stdlib + `numpy`, `Pillow`, `ImageHash`. **No torch, no CLIP, no PyQt.** `ffmpeg`/`ffprobe` are found via
env `FFMPEG_BIN` / `FFPROBE_BIN`, else on PATH (helper `engine.tools.ffmpeg_bin()` / `ffprobe_bin()`).

## Package layout (`engine/`)

| Module | Public API |
|---|---|
| `tools.py` | `ffmpeg_bin() -> str`, `ffprobe_bin() -> str`, `run(cmd: list[str], timeout: float = 60) -> subprocess.CompletedProcess` (bytes stdout/stderr, `CREATE_NO_WINDOW` only when `os.name == "nt"`, never raises on non-zero exit; raises `subprocess.TimeoutExpired`). |
| `events.py` | `class Emitter(human: bool = False, stream=sys.stdout)` with `.emit(type: str, **data)`. JSON mode writes `json.dumps({"type": type, **data}, ensure_ascii=False)` + `\n` and flushes. Human mode writes one readable line (`[type] key=value ...`). |
| `db.py` | `connect(path: str) -> sqlite3.Connection` (creates parent dir, `row_factory = sqlite3.Row`, `PRAGMA journal_mode=WAL`, `PRAGMA foreign_keys=ON`, runs `migrate`). `migrate(conn)`: list `MIGRATIONS: list[str]`, applies those with index >= `PRAGMA user_version`, sets user_version = len(MIGRATIONS). `now() -> str` (UTC ISO-8601, seconds). |
| `probe.py` | `class ProbeError(Exception)`. `probe(path: str) -> dict` with keys `duration: float, width: int, height: int, bit_rate: int, codec: str`. One call: `ffprobe -v error -select_streams v:0 -show_entries stream=width,height,codec_name,bit_rate:format=duration,bit_rate -of json <path>`. `bit_rate` = stream bit_rate if numeric else format bit_rate if numeric else 0 (ffprobe returns `"N/A"` or omits it for MPEG-TS). Raises `ProbeError` if no video stream, non-zero exit, or duration missing/<=0. |
| `scoring.py` | `score(meta: dict) -> float` = `width * height * max(bit_rate, 1)`. `pick_keeper(members: list[dict]) -> int` index of best by key `(score, size, -len(path), path)` descending on the first three, then path ascending (deterministic). Each member dict has `score, size, path`. |
| `fingerprint.py` | `SAMPLE_FRACTIONS = (0.1, 0.5, 0.9)`. `sample_times(duration) -> list[float]` = `[duration * f for f in SAMPLE_FRACTIONS]`. `extract_frame(path, t) -> PIL.Image.Image \| None`: `ffmpeg -v error -ss <t:.3f> -i <path> -frames:v 1 -f image2pipe -vcodec png -` and decode stdout with Pillow; returns None on failure/empty output (never writes temp files). `is_uniform(img, min_std=8.0) -> bool` (grayscale pixel std-dev < min_std, e.g. black/fade frames). `fingerprint(path, duration) -> list[tuple[int, int \| None, Image \| None]]`: per sample index `(idx, phash_as_int_or_None, image_or_None)`; hash is `None` when the frame is missing or uniform. pHash = `int(str(imagehash.phash(img)), 16)` (64-bit). `hamming(a: int, b: int) -> int`. |
| `grouping.py` | `find_groups(items, max_distance=10, min_frames=2, duration_tol=1.0) -> list[list[int]]`. `items`: list of `(file_id: int, duration: float, hashes: dict[int, int])` (frame_idx → hash, uniform/missing frames absent). Two files **match** iff `abs(d1-d2) <= max(duration_tol, 0.01*max(d1,d2))` AND the set of frame indices present in both has size >= `min_frames` AND every shared index has `hamming <= max_distance`. Groups = connected components (union-find) of the match graph, only components of size >= 2, members sorted by file_id, groups sorted by first member. Implementation: sort by duration and only compare within the tolerance window (near-linear). |
| `scan.py` | `run_scan(conn, roots: list[str], emit, settings: dict \| None = None, thumbs_dir: str \| None = None) -> int` (returns scan id; `settings["workers"]` >= 1, default 4). See "Scan". |
| `apply.py` | `apply_groups(conn, group_ids: list[int] \| None, dup_root: str, emit) -> str` (returns batch_id). `undo_batch(conn, batch_id: str, emit) -> int` (number restored). See "Apply / undo". |
| `queries.py` | `groups_json(conn, scan_id: int \| None = None, status: str \| None = None) -> list[dict]`, `history_json(conn) -> list[dict]`, `latest_scan_id(conn) -> int \| None`, `set_role(conn, group_id, file_id, role)`, `set_group_status(conn, group_id, status)`. |
| `cli.py` + `__main__.py` | `python -m engine <command> --db PATH [--human] ...`. See "CLI". |

`VIDEO_EXTS = {".mp4", ".ts", ".mkv", ".avi", ".mov", ".m4v", ".wmv", ".webm", ".flv"}` lives in `scan.py`; match case-insensitively.

## Schema (`db.MIGRATIONS[0]`)

```sql
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
```

Paths stored in the DB are `os.path.normcase(os.path.abspath(p))`. `root` is the normalised scan root the file was found under.

## Scan (`run_scan`)

Defaults `settings = {"max_distance": 10, "min_frames": 2, "duration_tol": 1.0, "method": "phash", "workers": 4}` (caller values override). `workers` < 1 or not an int → `ValueError` before any row is written.
1. Normalise roots (`normcase(abspath)`), drop duplicates and roots nested inside another root. Insert `scans` row (`status='running'`). Emit `scan_started {scan_id, roots}`.
2. Walk roots with `os.walk`, collect video files (ext check case-insensitive); skip any path inside `dup_root`-like dirs is NOT the engine's job (caller picks roots). Emit `files_found {count}`.
3. Main thread, in walk order: `stat` each file (emit `progress {phase:"fingerprint", done, total, path}` once per file). If a row exists with same `size` and `mtime` and `status='ok'` and has >=1 fingerprint with `method` → reuse (count `cached`). Otherwise the file is queued for analysis.
4. Analysis of queued files runs in a thread pool: one `ThreadPoolExecutor(max_workers=workers)` per drive key (`os.path.splitdrive(path)[0].upper()`), so each drive has at most `workers` files in flight. Workers run `probe` + `fingerprint`, pick the middle-index image (index 1, else any non-None image) and resize it to max 320px wide. Workers never touch the database. As each result completes, the main thread: upserts the row (by path, `status='new'`), saves the thumbnail as JPEG to `thumbs_dir/<file_id>.jpg` if `thumbs_dir` set (store path in `files.thumb`), deletes old fingerprints for that file/method, inserts one row per non-None hash (`db.u64_to_db`), sets meta + `score(meta)`, `status='ok'`, `error=NULL`, `probed_at=now()`, then emits `progress`. On a probe/fingerprint/any exception for that file: `status='unreadable'`, `error=str(e)`, emit `file_error {path, error}`, continue. Commit every 50 files and at the end. On KeyboardInterrupt or any other exception the pools are shut down with `cancel_futures=True` and the error propagates. File ids of new rows follow completion order; nothing else stored depends on processing order.
5. Files under these roots whose DB row is `ok` but which were not seen on disk → `status='missing'`.
6. Grouping over files of these roots with `status='ok'` (load hashes from `fingerprints`): `find_groups`. For each group insert `dup_groups` (method, pending) and members: keeper = `pick_keeper`, others `duplicate`. Emit `group {group_id, file_ids}` per group.
7. Update scan `status='done'`, `finished_at`, `stats` JSON `{files, cached, unreadable, groups, duplicates}`. Emit `scan_done {scan_id, ...stats}`. On KeyboardInterrupt: scan `cancelled`, re-raise. Other exception: `failed`, re-raise.

Scan **never moves or deletes video files**.

## Apply / undo

`apply_groups(conn, group_ids, dup_root, emit)`: target groups = given ids, or all groups with status `confirmed` when `None`. Only groups in status `pending|confirmed` are processed (others skipped with event `skip`). `batch_id = uuid4().hex`. For each `duplicate` member whose file `status='ok'`:
- `rel = os.path.relpath(file.path, file.root)`; root label = `os.path.basename(root.rstrip("\\/"))` or the drive letter (`"C"`) when basename is empty; `dst = join(dup_root, label, rel)`. If `dst` exists, insert `_1`, `_2`, ... before the extension until free. **Never overwrite.**
- Insert `actions(op='move', ok=NULL)` and **commit before moving**. `os.makedirs(dirname(dst), exist_ok=True)`, `shutil.move(src, dst)`. Then `ok=1`, file `status='moved'`, `files.path` unchanged (it is the original location). On error `ok=0, error=...`, emit `file_error`.
- Emit `progress {phase:"apply", done, total, path}` per file. Group status → `applied` when all its moves succeeded.
- Emit `apply_done {batch_id, moved, failed}`.

`undo_batch(conn, batch_id, emit)`: for `actions` of that batch with `op='move' AND ok=1`, in reverse id order: if `src` exists → error (never overwrite); else makedirs + `shutil.move(dst, src)`, insert `actions(op='restore', ok=1)` with the same batch_id, file `status='ok'`. Groups of restored files → `pending`. Emit `undo_done {batch_id, restored, failed}`.

## CLI (`python -m engine`)

Global options go **before** the subcommand: `python -m engine --db PATH [--human] <command> ...`. Exit code 0 on success, 1 on error (also emits `error {message}`).

| Command | Args | Output events |
|---|---|---|
| `scan` | `--root DIR` (repeatable, required), `--thumbs DIR` (default `<db dir>/thumbs`), `--max-distance N`, `--min-frames N`, `--workers N` (files analysed in parallel per drive, default 4) | scan events above |
| `groups` | `--scan ID` (default latest), `--status S` | one `groups {scan_id, groups:[...]}` |
| `set-role` | `--group G --file F --role keeper\|duplicate\|ignored` | `ok {}` (setting a new keeper demotes the old keeper to `duplicate`) |
| `confirm` | `--group G` (repeatable) or `--all-pending` | `ok {confirmed: n}` |
| `dismiss` | `--group G` | `ok {}` |
| `apply` | `--dup-root DIR`, `--group G` (repeatable; default all confirmed) | apply events |
| `undo` | `--batch ID` | undo events |
| `history` | — | `history {batches:[{batch_id, at, moved, restored, failed}]}` |

`groups_json` item: `{id, scan_id, status, members:[{file_id, path, role, size, duration, width, height, bit_rate, codec, score, thumb, status}]}` with keeper first, then by score desc.

## Electron app (`app/`)

Vite + React + TypeScript renderer, Electron main in TypeScript, `electron-builder` for packaging. Main process spawns the engine:
- dev: `<repo>/.venv/Scripts/python.exe -m engine ...` with `cwd = <repo>` (POSIX: `.venv/bin/python`).
- packaged: `process.resourcesPath/engine/engine.exe ...`; `FFMPEG_BIN`/`FFPROBE_BIN` set to `process.resourcesPath/ffmpeg/ffmpeg(.exe)` / `ffprobe(.exe)` if those files exist.
- DB: `app.getPath('userData')/library.db`. Settings (roots, dupRoot) in `app.getPath('userData')/settings.json`.
- Every engine call: parse stdout line by line as JSON, forward each event to the renderer via `webContents.send('engine:event', ev)`; resolve with all events + exit code. Cancel = kill child.
- Preload exposes `window.api` (contextIsolation on, nodeIntegration off): `pickFolder()`, `getSettings()`, `saveSettings(s)`, `run(args: string[]) -> Promise<{code, events}>`, `cancel()`, `onEvent(cb) -> unsubscribe`, `openPath(p)`, `showInFolder(p)`, `fileUrl(p)` (thumbnails served via a custom `thumb://` protocol or `file://`).

Screens: **Folders** (list roots, add via dialog or drag-drop, remove; duplicates folder picker), **Scan** (start, live progress bar per phase, current file, cancel, summary), **Review** (group cards: thumbnail + path + resolution + duration + bitrate + size per member, keeper badge, "Make keeper", "Ignore", per-group "Confirm" / "Dismiss", and a top **"Accept all suggestions"** button = `confirm --all-pending`), **Apply** (count of confirmed groups/files, target folder, confirm dialog, progress, result), **History** (batches with Undo).

## Convert to MP4 (`convert.py`, CLI `convert`, app tab **Convert**)

Purpose: turn playable non-MP4 videos (mainly MPEG-TS `.ts`) into `.mp4`. The engine never deletes or overwrites; the
app may move a source to the Recycle Bin after a verified success, only when the user ticks that option.

`engine/convert.py` public API:
- `nvenc_available() -> bool`: `h264_nvenc` listed in `ffmpeg -hide_banner -encoders` AND a 1-frame test encode
  (`-f lavfi -i color=s=256x256:d=1 -frames:v 1 -c:v h264_nvenc -f null -`) exits 0. Cached per process.
- `output_path(src, out_dir=None) -> str`: `<out_dir or dirname(src)>/<stem>.mp4`; if that path exists (or is `src`
  itself), use `<stem>_1.mp4`, `_2`, ... **Never returns an existing path.**
- `convert_file(src, dst, mode="copy", encoder="auto", on_percent=None) -> dict`:
  - writes to `dst + ".partial"` first (ffmpeg `-f mp4` because of that suffix), then `os.replace` to `dst` only after
    verification. On any failure the `.partial` is removed and `ConvertError(message)` is raised.
  - mode `copy`: `ffmpeg -v error -nostdin -y -i src -map 0:v:0 -map 0:a? -c copy -movflags +faststart -f mp4 <partial>`
    (drops data streams such as timed_id3, which MP4 cannot hold).
  - mode `reencode`: same maps, `-c:a aac -b:a 192k`, video `-c:v h264_nvenc -preset p5 -cq 23` when encoder is `nvenc`,
    or `auto` and `nvenc_available()`; else `-c:v libx264 -preset medium -crf 20`. `encoder="nvenc"` when unavailable →
    ConvertError.
  - progress: add `-progress pipe:1 -nostats`; parse `out_time_us=` (or `out_time_ms=`, also microseconds) lines and call
    `on_percent(int 0..100)` relative to the source duration from `probe`. Read stdout incrementally (Popen), stderr kept
    for the error message. Use `tools.ffmpeg_bin()`; `CREATE_NO_WINDOW` on Windows (same as `tools.run`).
  - verification: `probe(dst_partial)` succeeds and `abs(out.duration - src.duration) <= max(1.0, 0.01*src.duration)`.
  - returns `{"input", "output", "mode", "encoder" ("copy"|"nvenc"|"x264"), "seconds", "size_in", "size_out"}`.
- `run_convert(conn, inputs: list[str], emit, mode="copy", encoder="auto", out_dir=None) -> dict`: processes inputs
  sequentially. Skips (event `skip {path, reason}`) inputs that do not exist or already end with `.mp4`.
  Events: `convert_started {total, mode}`; per file `progress {phase:"convert", done, total, path, percent}` (several per
  file while percent changes; `done` = files finished so far); `converted {...convert_file result}`;
  `file_error {path, error}`; finally `convert_done {converted, failed, skipped, outputs:[output paths]}`. Returns the same
  dict as convert_done. `conn` is used only for `--from-scan-ext` resolution in the CLI (may be None otherwise).

CLI: `python -m engine --db X convert [--input FILE ...] [--from-scan-ext .ts] [--mode copy|reencode]
[--encoder auto|nvenc|x264] [--out-dir DIR]`. `--from-scan-ext EXT` adds every file of the latest done scan with
`status='ok'` whose path ends with EXT (case-insensitive) under that scan's roots. At least one input is required
(else `error`, exit 1).

App tab **Convert** (`app/src/screens/Convert.tsx`, added to the tab bar after History):
- File list: "Add files..." (`window.api.pickFiles()` → multi-select dialog filtered to video extensions), drag-drop
  files, "Add .ts from last scan" (fills the list only: run `["files", "--ext", ".ts"]` and add the returned paths),
  remove/clear.
- Options: mode (Copy — fast, no quality loss / Re-encode), encoder (Auto / NVIDIA GPU / CPU x264) shown only for
  re-encode, output (next to source / choose folder), checkbox "Move original to Recycle Bin after successful
  conversion" (default off).
- Convert → `run(["convert", ...inputs.flatMap(p => ["--input", p]), "--mode", m, "--encoder", e, (out dir)])`, live
  per-file percent + overall done/total, Cancel (kills the engine; a leftover `<dst>.partial` is harmless and
  `convert_file` deletes any existing `<dst>.partial` before it starts writing).
  Results table: input → output, size before/after, status. For each `converted` event, if the checkbox is ticked,
  call `window.api.trash(input)` (main: `shell.trashItem`).
- New `window.api` methods: `pickFiles(): Promise<string[]>`, `trash(path): Promise<void>`.

Engine helper command for the app: `files --ext EXT` → event `files {paths:[...]}` = files of the latest done scan with
`status='ok'` whose path ends with EXT (case-insensitive).

## Fresh scan, clear cache, files from folders

**`scan --fresh`** (`run_scan(..., settings={"fresh": True})`, default False, stored in scans.settings): skip the
cache check for every file — every file is probed + fingerprinted again (counted as not cached), its old fingerprints
replaced. Everything else (groups, events) unchanged.

**`clear-cache`** (new `engine/maintenance.py`: `clear_cache(conn, thumbs_dir) -> dict`): deletes ALL rows of
`fingerprints`; sets on every `files` row with status `ok` or `unreadable`: `status='new', error=NULL, duration=NULL,
width=NULL, height=NULL, bit_rate=NULL, codec=NULL, score=NULL, thumb=NULL, probed_at=NULL`; deletes `*.jpg` files
directly inside `thumbs_dir` (no recursion, nothing else). It does NOT touch `scans`, `dup_groups`, `group_members`,
`actions`, or rows with status `moved`/`missing` (History and Undo keep working). Refuses (raises, CLI `error`, exit 1)
while any scan row has status `running` **and** started less than 6 hours ago. Event: `cache_cleared {fingerprints,
files_reset, thumbs_deleted}`. CLI: `python -m engine --db X clear-cache [--thumbs DIR]` (default `<db dir>/thumbs`).

**`files --root DIR`** (repeatable; alternative to `--ext`-from-scan): walk the given folders recursively and return
video files from disk (not the DB): extensions = `scan.VIDEO_EXTS` minus `.mp4` by default, or only `--ext EXT` when
given; case-insensitive; paths via `os.path.realpath` (keeps casing); sorted; de-duplicated. Same event
`files {paths}`. Implemented as `convert.video_files_under(roots, ext=None) -> list[str]`. `--root` and the scan-based
mode can't be mixed: with `--root`, `--ext` only filters.

App:
- **Scan** screen: checkbox "Fresh scan (ignore cache)" → adds `--fresh`; button "Clear cache…" (confirm dialog
  explaining: forgets fingerprints/metadata/thumbnails, keeps History/Undo, next scan re-reads every file) →
  `run(["clear-cache"])`, shows the counts from `cache_cleared`. Both disabled while a scan runs.
- **Convert** screen: button "Add folders..." next to "Add files..." → `window.api.pickFolders()` (already exists,
  multi-select) → `run(["files", ...folders.flatMap(f => ["--root", f])])` → add returned paths (same de-dup as
  other adders) and show "Found N file(s) in M folder(s)".
