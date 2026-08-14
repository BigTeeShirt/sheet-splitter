"""Build a synthetic sublimation sheet to develop and smoke-test against.

Stands in for a real export until one is available: CMYK, zip-compressed, 300dpi,
irregular pieces each ringed by a thick black laser line, printed labels floating
inside some pieces, and registration specks that must NOT become files.

    python3 tools/make_test_sheet.py out.tif --inches 24x36 --pieces 12
"""

import argparse
import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

Image.MAX_IMAGE_PIXELS = None

LINE_IN = 0.05  # thick black cut line, consistent thickness


def piece_polygon(cx, cy, w, h, rng):
    """A garment-panel-ish outline: a rounded, slightly irregular blob."""
    pts, n = [], 14
    for i in range(n):
        a = 2 * math.pi * i / n
        r = 0.85 + rng.uniform(-0.12, 0.12)
        pts.append((cx + math.cos(a) * w / 2 * r, cy + math.sin(a) * h / 2 * r))
    return pts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--inches", default="24x36", help="sheet size, e.g. 44x120")
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--pieces", type=int, default=12)
    ap.add_argument("--cols", type=int, default=3)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    win, hin = (float(v) for v in args.inches.lower().split("x"))
    W, H = int(win * args.dpi), int(hin * args.dpi)
    line = max(2, int(LINE_IN * args.dpi))

    # Blank media is zero ink in CMYK.
    sheet = Image.new("CMYK", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(sheet)

    cols = args.cols
    rows = math.ceil(args.pieces / cols)
    cw, ch = W / cols, H / rows
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from sheetsplit.core import load_font
    font = load_font(int(args.dpi * 0.35))

    made = 0
    for r in range(rows):
        for c in range(cols):
            if made >= args.pieces:
                break
            made += 1
            cx, cy = cw * (c + 0.5), ch * (r + 0.5)
            w, h = cw * 0.78, ch * 0.78
            poly = piece_polygon(cx, cy, w, h, rng)

            # all-over art: a couple of soft ink fields so the piece is not flat
            fill = (rng.randint(20, 200), rng.randint(20, 200),
                    rng.randint(20, 200), rng.randint(0, 40))
            draw.polygon(poly, fill=fill)
            draw.ellipse(
                [cx - w * 0.2, cy - h * 0.2, cx + w * 0.2, cy + h * 0.2],
                fill=(rng.randint(0, 255), rng.randint(0, 255), 30, 10),
            )
            # the thick black line the laser follows
            draw.line(poly + [poly[0]], fill=(0, 0, 0, 255), width=line, joint="curve")

            # a printed label inside the piece, disconnected from the art:
            # its own blob, and must not become its own file
            if made % 3 == 0:
                draw.text((cx - w * 0.15, cy + h * 0.25), f"PC{made:02d}",
                          fill=(0, 0, 0, 255), font=font)

    # registration specks around the edges -- below the minimum size, must be dropped
    for i in range(8):
        x = rng.randint(int(W * 0.02), int(W * 0.98))
        y = rng.choice([int(H * 0.01), int(H * 0.99)])
        d = int(args.dpi * 0.06)
        draw.ellipse([x, y, x + d, y + d], fill=(0, 0, 0, 255))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, "TIFF", compression="tiff_deflate",
               dpi=(args.dpi, args.dpi))
    mb = out.stat().st_size / 1e6
    print(f"{out}  {W}x{H}px  {win:g}x{hin:g}in @ {args.dpi}dpi  "
          f"{args.pieces} pieces  {mb:.0f}MB")


if __name__ == "__main__":
    main()
