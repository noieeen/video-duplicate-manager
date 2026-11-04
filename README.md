# Video Duplicate Manager

Finds duplicate videos across folders and drives, including re-encodes and copies at other resolutions. It suggests which copy to keep and moves the others into a duplicates folder, but only after you confirm. Every move is logged and can be undone.

- **Detection:** a perceptual hash (pHash) of 3 frames at 10% / 50% / 90% of the duration. Black or fade frames are ignored, so unrelated videos don't match on a black frame. Short videos work.
- **Keeper choice:** the highest width × height × bitrate (bitrate read from the container for `.ts`). Ties go to the larger file.
- **Safety:** scanning never touches your files. Moves happen only for groups you confirm, never overwrite anything, and each batch can be undone.
- **Library:** a SQLite database caches probes and fingerprints by size + mtime, so a re-scan only processes new or changed files.

## Use the app (Windows)

Install from the `Video Duplicate Manager Setup *.exe` (a GitHub Actions artifact, or build it yourself below). Then:

1. **Folders:** add the source folders (drag-and-drop works) and pick a duplicates folder *outside* them.
2. **Scan:** shows progress. Nothing is moved at this step.
3. **Review:** each group shows thumbnails, resolution, bitrate and size. You can change the keeper, ignore a file, Confirm or Dismiss a group, or **Accept all suggestions**.
4. **Apply:** moves the duplicates of confirmed groups to `<duplicates folder>/<source folder name>/<relative path>`.
5. **History:** undo any batch.

The app's data (library DB, thumbnails, settings) lives in `%APPDATA%\Video Duplicate Manager`.

## Develop

Requirements: Python 3.12, Node 22+, and `ffmpeg`/`ffprobe` on PATH.

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
python -m pytest -q
```

```bash
cd app
npm install
npm run dev
```

In dev, the app runs the engine with `<repo>\.venv\Scripts\python.exe -m engine`.

### Engine CLI

```bash
python -m engine --db library.db --human scan --root D:\videos --root E:\backup
python -m engine --db library.db --human groups
python -m engine --db library.db confirm --all-pending
python -m engine --db library.db apply --dup-root D:\duplicates
python -m engine --db library.db history
python -m engine --db library.db undo --batch <batch_id>
```

Without `--human` the engine prints JSON lines; that is the app's protocol. The full contract is in [docs/ENGINE_SPEC.md](docs/ENGINE_SPEC.md).

### Docker (headless)

See the header of [docker-compose.yml](docker-compose.yml). In short:

```bash
docker compose run --rm engine scan --root /videos
```

### Build the installer

```powershell
.\scripts\build_all.ps1
```

This runs PyInstaller on the engine (`dist-engine/`), copies `ffmpeg`/`ffprobe` into `vendor/ffmpeg/`, then runs electron-builder (NSIS). The output goes to `app/release/`. CI does the same in `.github/workflows/build.yml`.

## Layout

- `engine/`: Python engine (probe, fingerprint, grouping, scoring, scan, apply/undo, SQLite, CLI)
- `app/`: Electron + Vite + React + TypeScript UI. It spawns the engine and renders its events.
- `tests/`: pytest suite on synthetic videos generated with ffmpeg
- `scripts/`: packaging
- `docs/`: spec, review, plan

## License

MIT
