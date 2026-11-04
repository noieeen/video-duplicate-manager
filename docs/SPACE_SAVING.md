# Saving space: research and measurements (2026-11-05)

Question: what is the best way to shrink this library, with a hard cap of **1080p and 60 fps**?
Online research (summarised below) plus measurements on the owner's machine (RTX 4070, driver 610.88, ffmpeg 7.1.1).
Implemented as Convert mode **Shrink** (`convert --mode shrink`) and the **Space** tab.

## Where the space is (owner's library, 3,312 readable files, 6.61 TB)

| Group | Files | Size | Share |
|---|---|---|---|
| Above 1080p (short side > 1080) | 83 | 0.38 TB | 5.8% |
| 1080p-class H.264, 4–8 Mb/s | 1,870 | 4.39 TB | 66.4% |
| H.264 ≤ 4 Mb/s | 1,356 | 1.84 TB | 27.8% |
| Already HEVC/AV1 | 5 | 0.01 TB | 0.1% |

The 1080p/60 cap alone touches only ~6% of the library. The real space is in ordinary 1080p H.264 at ~5 Mb/s.

## Measured: one real 1080p H.264 clip (60 s, 30 fps, progressive; source .ts 37.8 MB incl. audio)

Video only, NVENC, SSIM against the source (1.0 = identical):

| Encode | Size | SSIM | Speed |
|---|---|---|---|
| H.264 cq28 p5 | 37.6 MB | 0.988 | ~12x |
| HEVC cq27 p5 10-bit (common recommendation) | 35.9 MB | 0.988 | ~10x |
| HEVC cq30 p5 10-bit | 24.8 MB | 0.985 | 9.8x |
| AV1 cq32 p5 10-bit | 34.7 MB | 0.989 | ~9x |
| **AV1 cq36 p5 10-bit** | **23.8 MB** | **0.987** | 9.3x |
| AV1 cq36 p1 10-bit | — | — | 18.3x |

- Typical "recommended" CQ values (HEVC 26–28, AV1 30–32) barely shrink an already-moderate 5 Mb/s source.
  You need about **AV1 cq36 / HEVC cq30** for ~30–35% savings. AV1 cq36 is smaller *and* scores better than HEVC cq30.
- Rough whole-library estimate if every 1080p-class H.264 file were re-encoded with AV1 cq36: ~1.3–1.5 TB saved out of
  4.4 TB, at about 190 h of encoding (p5) or ~100 h (p1). One NVENC engine, so there's no parallel speed-up.
- One clip is not a library: grainy or high-motion footage gains less. Validate on 5–10 varied files before a big batch.

## Compatibility (verified in this app's Electron build via `media://`)

HEVC 8/10-bit (`-tag:v hvc1`) and AV1 8/10-bit in MP4 all load and seek in the Review compare player.
Windows' own player needs the HEVC Video Extensions (paid/OEM) or the free AV1 Video Extension; VLC plays both.

## "Shrink" recipe (implemented as `convert --mode shrink`)

```
ffmpeg -i IN -map 0:v:0 -map 0:a:0? -map_metadata 0
  [-vf "scale='min(1920,iw)':'min(1080,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2"]  # only if > 1080p
  [-vf fps=60]                                                                                          # only if > 60 fps
  -c:v av1_nvenc -preset p5 -tune hq -rc vbr -cq 36 -b:v 0 -multipass fullres -spatial-aq 1 -rc-lookahead 32
  -highbitdepth 1 -pix_fmt p010le -c:a aac -b:a 128k -movflags +faststart OUT.mp4
```

- **Decision (owner):** "1080p" means the **short side ≤ 1080**, aspect ratio from the source. Landscape above 1080p →
  `scale=-2:1080`; portrait → `scale=1080:-2` (1080x1920 stays as is). The research's `min(1920,iw):min(1080,ih)` filter
  was rejected because it shrinks a 1080x1920 portrait clip to 608x1080.
- Add the fps filter only when the probed fps is above 60 (it is unsafe on variable-frame-rate sources).
- Deinterlace (`bwdif`) only when ffprobe's `field_order` isn't progressive (broadcast TS).
- **Keep rule:** keep the new file only if it is ≥ 15% smaller and the duration matches; otherwise delete the output
  and report "skipped (not smaller)". This is the same idea as ab-av1's `--max-encoded-percent` (default 80).
  A fixed CQ can make files bigger (we measured 1.6x for H.264 cq23), so the post-check is required.
- Skip HDR sources (they need colour metadata handling) and files that are already HEVC/AV1 within the cap.
- Audio: AAC 192k → 128k saves only ~5 MB/hour. Dropping extra tracks is a small saving with data-loss risk, so it's opt-in.
- Software encoders (SVT-AV1, x265) give smaller files at the same quality but are many times slower: archive jobs only.

## Sources (from the research agent; several claims were general knowledge, not sourced)

- Gough Lui, av1_nvenc round-up (2024): https://goughlui.com/2024/01/07/video-codec-round-up-2023-part-13-av1_nvenc-av1-nvidia-nvenc/
- ab-av1 (VMAF-targeted CRF search, `--max-encoded-percent`): https://github.com/alexheretic/ab-av1
- HEVC hardware decoding in Chromium: https://github.com/StaZhu/enable-chromium-hevc-hardware-decoding
- Phoronix, FFmpeg NVENC AV1: https://www.phoronix.com/news/NVIDIA-NVENC-AV1-FFmpeg
