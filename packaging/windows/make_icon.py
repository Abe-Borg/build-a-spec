"""Generate the Build-a-Spec application icon.

Produces ``assets/BuildASpec.ico`` (multi-resolution, consumed by the
PyInstaller build and the Inno Setup installer) and ``assets/BuildASpec.png``
(a 512px master, handy for READMEs / release pages). Pure Pillow, run at
build/design time only — Pillow is deliberately NOT a runtime dependency,
so this script is not part of ``requirements.txt``.

    pip install Pillow
    python packaging/windows/make_icon.py

Design: a modern rounded app tile in the app's indigo, holding a white
"specification" sheet with a folded corner and ruled lines, plus a green
check badge — the app's confirmed/reviewed motif. Drawn at 4x and
downsampled (LANCZOS) so every embedded size stays crisp.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

SS = 1024  # supersample master canvas
ICON_SIZES = [16, 24, 32, 48, 64, 128, 256]

INDIGO_TOP = (99, 102, 241)     # indigo-500
INDIGO_BOTTOM = (67, 56, 202)   # indigo-700
SHEET = (255, 255, 255)
SHEET_FOLD = (219, 222, 244)    # subtle bluish shadow on the folded corner
RULE = (165, 172, 232)          # indigo-tinted ruled lines
ACCENT = (16, 185, 129)         # emerald-500 check badge
ACCENT_RING = (255, 255, 255)


def _rounded_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, size - 1, size - 1), radius=radius, fill=255
    )
    return mask


def _vertical_gradient(size: int, top: tuple, bottom: tuple) -> Image.Image:
    grad = Image.new("RGB", (size, size), top)
    px = grad.load()
    for y in range(size):
        t = y / (size - 1)
        r = round(top[0] + (bottom[0] - top[0]) * t)
        g = round(top[1] + (bottom[1] - top[1]) * t)
        b = round(top[2] + (bottom[2] - top[2]) * t)
        for x in range(size):
            px[x, y] = (r, g, b)
    return grad


def render_master() -> Image.Image:
    img = Image.new("RGBA", (SS, SS), (0, 0, 0, 0))

    # --- app tile: indigo gradient clipped to a rounded square ---
    tile = _vertical_gradient(SS, INDIGO_TOP, INDIGO_BOTTOM).convert("RGBA")
    tile.putalpha(_rounded_mask(SS, radius=int(SS * 0.22)))
    img.alpha_composite(tile)

    draw = ImageDraw.Draw(img)

    # --- specification sheet with a folded top-right corner ---
    sx0, sy0, sx1, sy1 = int(SS * 0.28), int(SS * 0.22), int(SS * 0.72), int(SS * 0.78)
    fold = int(SS * 0.12)
    radius = int(SS * 0.03)

    # body (leave the folded corner out by drawing the rect then the fold)
    draw.rounded_rectangle((sx0, sy0, sx1, sy1), radius=radius, fill=SHEET)
    # knock out the top-right corner and redraw it as a folded triangle
    draw.polygon(
        [(sx1 - fold, sy0), (sx1, sy0), (sx1, sy0 + fold)],
        fill=(0, 0, 0, 0),
    )
    # re-fill the sheet minus the fold using a polygon that traces the page
    draw.polygon(
        [
            (sx0 + radius, sy0),
            (sx1 - fold, sy0),
            (sx1, sy0 + fold),
            (sx1, sy1 - radius),
            (sx0, sy1 - radius),
            (sx0, sy0 + radius),
        ],
        fill=SHEET,
    )
    # rounded bottom corners cleanup
    draw.rounded_rectangle((sx0, sy0 + radius, sx1, sy1), radius=radius, fill=SHEET)
    # the folded flap (a small triangle, shaded)
    draw.polygon(
        [(sx1 - fold, sy0), (sx1 - fold, sy0 + fold), (sx1, sy0 + fold)],
        fill=SHEET_FOLD,
    )

    # --- ruled lines (varying widths, like specification text) ---
    line_h = int(SS * 0.028)
    gap = int(SS * 0.072)
    lx0 = sx0 + int(SS * 0.06)
    widths = [0.74, 0.86, 0.60, 0.80, 0.46]
    ly = sy0 + int(SS * 0.20)
    usable = (sx1 - lx0) - int(SS * 0.06)
    for w in widths:
        draw.rounded_rectangle(
            (lx0, ly, lx0 + int(usable * w), ly + line_h),
            radius=line_h // 2,
            fill=RULE,
        )
        ly += gap

    # --- emerald check badge, bottom-right, overlapping the sheet ---
    bcx, bcy = int(SS * 0.70), int(SS * 0.71)
    br = int(SS * 0.135)
    draw.ellipse(
        (bcx - br - int(SS * 0.02), bcy - br - int(SS * 0.02),
         bcx + br + int(SS * 0.02), bcy + br + int(SS * 0.02)),
        fill=ACCENT_RING,
    )
    draw.ellipse((bcx - br, bcy - br, bcx + br, bcy + br), fill=ACCENT)
    # checkmark
    cw = int(SS * 0.028)
    draw.line(
        [
            (bcx - int(br * 0.45), bcy + int(br * 0.02)),
            (bcx - int(br * 0.08), bcy + int(br * 0.40)),
            (bcx + int(br * 0.50), bcy - int(br * 0.38)),
        ],
        fill=(255, 255, 255),
        width=cw,
        joint="curve",
    )

    return img


def main() -> int:
    out_dir = Path(__file__).resolve().parent / "assets"
    out_dir.mkdir(parents=True, exist_ok=True)

    master = render_master()

    png_path = out_dir / "BuildASpec.png"
    master.resize((512, 512), Image.LANCZOS).save(png_path)

    ico_path = out_dir / "BuildASpec.ico"
    # Save the full-resolution master with the size list; Pillow downsamples
    # to every embedded resolution. (Saving from a small base image would
    # silently drop the larger sizes — the base can't be upscaled.)
    master.save(ico_path, format="ICO", sizes=[(s, s) for s in ICON_SIZES])

    print(f"wrote {ico_path} ({', '.join(f'{s}x{s}' for s in ICON_SIZES)})")
    print(f"wrote {png_path} (512x512)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
