"""Generate the app icon (app/build/icon.png and app/build/icon.ico).

Design: rounded dark-blue square, two overlapping white rounded "film frame"
rectangles offset diagonally, and a small play triangle in the front frame.
Drawn at 1024px and downscaled with LANCZOS for smooth edges.

Usage: .venv/Scripts/python.exe scripts/make_icon.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "app" / "build"

BIG = 1024
BG = (0x1E, 0x3A, 0x8A, 255)  # #1e3a8a
WHITE = (255, 255, 255, 255)
# Back frame: white at 60% over the blue background, pre-blended (ImageDraw does not alpha-blend).
BACK_FRAME = (165, 176, 208, 255)

ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def draw_icon() -> Image.Image:
    img = Image.new("RGBA", (BIG, BIG), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Rounded square background.
    draw.rounded_rectangle((0, 0, BIG - 1, BIG - 1), radius=230, fill=BG)

    frame_w, frame_h, frame_r = 440, 320, 56

    # Back frame, offset up-left.
    back_x, back_y = 200, 250
    draw.rounded_rectangle(
        (back_x, back_y, back_x + frame_w, back_y + frame_h),
        radius=frame_r,
        fill=BACK_FRAME,
    )

    # Front frame, offset down-right, solid white.
    front_x, front_y = 384, 454
    draw.rounded_rectangle(
        (front_x, front_y, front_x + frame_w, front_y + frame_h),
        radius=frame_r,
        fill=WHITE,
    )

    # Play triangle centred in the front frame.
    cx = front_x + frame_w / 2 + 18  # nudge right so the triangle looks centred optically
    cy = front_y + frame_h / 2
    half_h = 88
    half_w = 80
    draw.polygon(
        [
            (cx - half_w * 0.6, cy - half_h),
            (cx - half_w * 0.6, cy + half_h),
            (cx + half_w * 0.9, cy),
        ],
        fill=BG,
    )
    return img


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    big = draw_icon()

    png = big.resize((512, 512), Image.Resampling.LANCZOS)
    png_path = OUT_DIR / "icon.png"
    png.save(png_path, format="PNG")

    ico_path = OUT_DIR / "icon.ico"
    big.save(ico_path, format="ICO", sizes=ICO_SIZES)

    print(f"wrote {png_path} ({png.size[0]}x{png.size[1]})")
    print(f"wrote {ico_path} (sizes: {', '.join(f'{w}' for w, _ in ICO_SIZES)})")


if __name__ == "__main__":
    main()
