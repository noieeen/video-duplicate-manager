import numpy as np
import pytest
from PIL import Image

from engine.fingerprint import _phash, extract_frame, fingerprint, hamming, sample_times


def _hashes(fp):
    return [h for _, h, _ in fp]


def test_sample_times_fractions():
    assert sample_times(100) == [10, 50, 90]


def test_same_video_reencoded_at_other_resolution_matches(tmp_path, make_video):
    a = make_video(tmp_path / "small.mp4", source="testsrc", w=320, h=240, duration=30)
    b = make_video(tmp_path / "big.mp4", source="testsrc", w=640, h=480, duration=30)
    ha = _hashes(fingerprint(a, 30))
    hb = _hashes(fingerprint(b, 30))
    assert all(h is not None for h in ha)
    assert all(h is not None for h in hb)
    for x, y in zip(ha, hb):
        assert hamming(x, y) <= 10


def test_different_content_has_a_distant_frame(tmp_path, make_video):
    a = make_video(tmp_path / "test.mp4", source="testsrc", duration=30)
    b = make_video(tmp_path / "mandel.mp4", source="mandelbrot", duration=30)
    ha = _hashes(fingerprint(a, 30))
    hb = _hashes(fingerprint(b, 30))
    pairs = [(x, y) for x, y in zip(ha, hb) if x is not None and y is not None]
    assert pairs
    assert any(hamming(x, y) > 10 for x, y in pairs)


def test_black_video_has_no_hashes(tmp_path, make_video):
    p = make_video(tmp_path / "black.mp4", source="black", duration=30)
    fp = fingerprint(p, 30)
    assert _hashes(fp) == [None, None, None]
    assert all(img is None for _, _, img in fp)


def test_short_20_second_video_is_fingerprinted(tmp_path, make_video):
    p = make_video(tmp_path / "short.mp4", source="testsrc", duration=20)
    assert all(h is not None for h in _hashes(fingerprint(p, 20)))


def test_extract_frame_missing_file_returns_none(tmp_path):
    assert extract_frame(str(tmp_path / "nope.mp4"), 1.0) is None


def _parity_images(tmp_path, make_video):
    rng = np.random.default_rng(1234)
    imgs = []
    # Random noise, varied sizes and modes.
    for i in range(100):
        w, h = int(rng.integers(8, 700)), int(rng.integers(8, 500))
        arr = rng.integers(0, 256, size=(h, w, 3), dtype=np.uint8)
        if i % 3 == 0:
            imgs.append(Image.fromarray(arr, "RGB"))
        elif i % 3 == 1:
            imgs.append(Image.fromarray(arr[:, :, 0], "L"))
        else:
            imgs.append(Image.fromarray(arr, "RGB").convert("RGBA"))
    # Smooth gradients at random angles.
    for _ in range(60):
        w, h = int(rng.integers(16, 400)), int(rng.integers(16, 400))
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
        a, b = rng.uniform(-1, 1, size=2)
        g = (a * xx / w + b * yy / h)
        g = (255 * (g - g.min()) / max(np.ptp(g), 1e-9)).astype(np.uint8)
        imgs.append(Image.fromarray(g, "L"))
    # Real frames from synthetic videos.
    for source in ("testsrc", "mandelbrot", "smpte"):
        path = make_video(tmp_path / f"{source}.mp4", source=source, duration=30)
        for t in np.linspace(0.5, 29.5, 20):
            img = extract_frame(path, float(t))
            assert img is not None
            imgs.append(img)
    return imgs


def test_phash_matches_imagehash_reference(tmp_path, make_video):
    imagehash = pytest.importorskip("imagehash")
    imgs = _parity_images(tmp_path, make_video)
    assert len(imgs) >= 200
    diffs = []
    for img in imgs:
        ref = int(str(imagehash.phash(img)), 16)
        diffs.append(hamming(_phash(img), ref))
    exact = sum(d == 0 for d in diffs)
    print(f"pHash parity: {exact}/{len(imgs)} exact, max hamming {max(diffs)}")
    assert max(diffs) <= 2
    assert exact / len(imgs) >= 0.99
