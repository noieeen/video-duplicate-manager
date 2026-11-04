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
| `scan.py` | `run_scan(conn, roots: list[str], emit, settings: dict \| None = None, thumbs_dir: str \| None = None) -> int` (returns scan id). See "Scan". |
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

Defaults `settings = {"max_distance": 10, "min_frames": 2, "duration_tol": 1.0, "method": "phash"}` (caller values override).
1. Normalise roots (`normcase(abspath)`), drop duplicates and roots nested inside another root. Insert `scans` row (`status='running'`). Emit `scan_started {scan_id, roots}`.
2. Walk roots with `os.walk`, collect video files (ext check case-insensitive); skip any path inside `dup_root`-like dirs is NOT the engine's job (caller picks roots). Emit `files_found {count}`.
3. For each file (emit `progress {phase:"fingerprint", done, total, path}` after each): `stat`. If a row exists with same `size` and `mtime` and `status='ok'` and has >=1 fingerprint with `method` → reuse (count `cached`). Else upsert row (by path), `probe` → store meta + `score(meta)`; `fingerprint` → delete old fingerprints for that file/method, insert one row per non-None hash; if `thumbs_dir` set, save the middle-index image (index 1, else any non-None image) as JPEG max 320px wide to `thumbs_dir/<file_id>.jpg` and store path in `files.thumb`; `status='ok'`, `error=NULL`, `probed_at=now()`. On `ProbeError`/timeout/any exception for that file: `status='unreadable'`, `error=str(e)`, emit `file_error {path, error}`, continue. Commit every 50 files and at the end.
4. Files under these roots whose DB row is `ok` but which were not seen on disk → `status='missing'`.
5. Grouping over files of these roots with `status='ok'` (load hashes from `fingerprints`): `find_groups`. For each group insert `dup_groups` (method, pending) and members: keeper = `pick_keeper`, others `duplicate`. Emit `group {group_id, file_ids}` per group.
6. Update scan `status='done'`, `finished_at`, `stats` JSON `{files, cached, unreadable, groups, duplicates}`. Emit `scan_done {scan_id, ...stats}`. On KeyboardInterrupt: scan `cancelled`, re-raise. Other exception: `failed`, re-raise.

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
| `scan` | `--root DIR` (repeatable, required), `--thumbs DIR` (default `<db dir>/thumbs`), `--max-distance N`, `--min-frames N` | scan events above |
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
