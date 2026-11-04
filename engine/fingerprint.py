"""Perceptual fingerprints of video frames: pHash of 3 frames sampled at 10%/50%/90% of duration.

Uniform frames (black, fade) are treated as missing so that they never match each other.
Frames are decoded from ffmpeg stdout; no temp files are written.
"""
from __future__ import annotations

import io
import subprocess

import numpy as np
from PIL import Image

from engine import tools

SAMPLE_FRACTIONS = (0.1, 0.5, 0.9)

# pHash parameters (same as imagehash.phash defaults: hash_size=8, highfreq_factor=4).
_PHASH_SIZE = 32
_PHASH_LOW = 8
# Unnormalised DCT-II basis: _DCT[k, n] = 2 * cos(pi * k * (2n + 1) / (2N)), restricted to the low-frequency rows.
_DCT = 2 * np.cos(
    np.pi * np.arange(_PHASH_LOW)[:, None] * (2 * np.arange(_PHASH_SIZE) + 1)[None, :] / (2 * _PHASH_SIZE)
)


def sample_times(duration: float) -> list[float]:
    return [duration * f for f in SAMPLE_FRACTIONS]


def extract_frame(path: str, t: float) -> Image.Image | None:
    # Fast input seek first. Containers without a seek index (MPEG-TS) and sparse keyframes can
    # yield no frame that way; then fall back to an accurate output seek (decodes from the start).
    seek = f"{t:.3f}"
    out = ["-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"]
    for cmd in (
        [tools.ffmpeg_bin(), "-v", "error", "-ss", seek, "-i", path, *out],
        [tools.ffmpeg_bin(), "-v", "error", "-i", path, "-ss", seek, *out],
    ):
        try:
            proc = tools.run(cmd, timeout=120)
        except (subprocess.TimeoutExpired, OSError):
            return None
        if proc.returncode == 0 and proc.stdout:
            break
    else:
        return None
    try:
        img = Image.open(io.BytesIO(proc.stdout))
        img.load()
    except Exception:
        return None
    return img


def is_uniform(img: Image.Image, min_std: float = 8.0) -> bool:
    gray = np.asarray(img.convert("L"), dtype=np.float64)
    return float(gray.std()) < min_std


def _phash(img: Image.Image) -> int:
    """64-bit pHash, bit-identical to ``int(str(imagehash.phash(img)), 16)``."""
    gray = img.convert("L").resize((_PHASH_SIZE, _PHASH_SIZE), Image.Resampling.LANCZOS)
    pixels = np.asarray(gray, dtype=np.float64)
    low = _DCT @ pixels @ _DCT.T
    # Coefficients that are mathematically equal to the median (e.g. exact zeros on smooth gradients)
    # come out of the matrix product with float noise; treat that noise as a tie, as scipy's DCT does.
    med = np.median(low)
    tol = 1e-9 * max(1.0, float(np.abs(low).max()))
    bits = (low > med + tol).ravel()
    return int.from_bytes(np.packbits(bits).tobytes(), "big")


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def fingerprint(path: str, duration: float) -> list[tuple[int, int | None, Image.Image | None]]:
    """Per sample index: (idx, phash or None, image or None).

    Hash and image are None when the frame is missing or uniform (the image is dropped too,
    so callers picking a thumbnail never get a black frame).
    """
    out: list[tuple[int, int | None, Image.Image | None]] = []
    for idx, t in enumerate(sample_times(duration)):
        img = extract_frame(path, t)
        if img is None or is_uniform(img):
            out.append((idx, None, None))
        else:
            out.append((idx, _phash(img), img))
    return out
