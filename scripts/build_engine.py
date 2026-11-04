"""Freeze the engine with PyInstaller and bundle ffmpeg/ffprobe for the Electron app.

Outputs:
  dist-engine/engine/engine.exe   (PyInstaller --onedir; app/package.json copies dist-engine/engine -> resources/engine)
  vendor/ffmpeg/ffmpeg.exe
  vendor/ffmpeg/ffprobe.exe       (app/package.json copies vendor/ffmpeg -> resources/ffmpeg)

Run from anywhere: python scripts/build_engine.py  (uses the interpreter running this script).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DIST = REPO / "dist-engine"
WORK = REPO / "build-engine"
VENDOR_FFMPEG = REPO / "vendor" / "ffmpeg"
ENGINE_EXE = DIST / "engine" / "engine.exe"


def run_pyinstaller() -> None:
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onedir", "--console",
        "--name", "engine",
        "--distpath", str(DIST),
        "--workpath", str(WORK),
        "--specpath", str(WORK),
        "--paths", str(REPO),
        "--collect-submodules", "engine",
        # scipy's array_api_compat imports torch optionally; the dev venv has torch installed,
        # which would otherwise bloat the bundle by ~450 MB. The engine never uses it.
        "--exclude-module", "torch",
        "--exclude-module", "torchvision",
        "--exclude-module", "torchaudio",
        str(REPO / "scripts" / "engine_entry.py"),
    ]
    print("[build_engine] running PyInstaller", flush=True)
    subprocess.run(cmd, cwd=REPO, check=True)
    if not ENGINE_EXE.is_file():
        raise SystemExit(f"[build_engine] PyInstaller did not produce {ENGINE_EXE}")


def follow_scoop_shim(path: Path) -> Path:
    """Scoop puts small launcher .exe files in shims/ with a sibling <name>.shim naming the real binary."""
    shim = path.with_suffix(".shim")
    if not shim.is_file():
        return path
    for line in shim.read_text(encoding="utf-8", errors="replace").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == "path":
            return Path(value.strip().strip('"')).resolve()
    return path


def find_tool(env_name: str, exe: str) -> Path:
    raw = os.environ.get(env_name) or shutil.which(exe)
    if not raw:
        raise SystemExit(
            f"[build_engine] {exe} not found. Put it on PATH or set {env_name} to its full path."
        )
    path = follow_scoop_shim(Path(raw).resolve())
    if not path.is_file():
        raise SystemExit(f"[build_engine] {env_name}/{exe} points to a missing file: {path}")
    return path


def copy_ffmpeg() -> list[Path]:
    VENDOR_FFMPEG.mkdir(parents=True, exist_ok=True)
    copied = []
    for env_name, exe in (("FFMPEG_BIN", "ffmpeg"), ("FFPROBE_BIN", "ffprobe")):
        src = find_tool(env_name, exe)
        dst = VENDOR_FFMPEG / f"{exe}.exe"
        if src.resolve() != dst.resolve():
            shutil.copy2(src, dst)
        probe = subprocess.run([str(dst), "-version"], capture_output=True, check=False)
        if probe.returncode != 0:
            raise SystemExit(f"[build_engine] copied {dst} does not run (exit {probe.returncode})")
        print(f"[build_engine] {exe}: {src} -> {dst}", flush=True)
        copied.append(dst)
    return copied


def main() -> int:
    run_pyinstaller()
    ffmpeg_files = copy_ffmpeg()
    print("[build_engine] outputs:")
    print(f"  {ENGINE_EXE}")
    for p in ffmpeg_files:
        print(f"  {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
