"""Find the black-outlined pieces on a sublimation sheet and cut each one out.

The sheets are flat CMYK zip-compressed TIFFs, 500MB-2GB, one garment size per
sheet, every piece ringed by a thick black line for the laser cutter. The whole
performance story is that the sheet gets decompressed exactly once: detection
runs on a small copy, but every crop is taken from the one in-memory original.
"""

from __future__ import annotations

import json
import logging
import math
import os
import shutil
import sys
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage

# Print-resolution sheets are hundreds of megapixels. Pillow's decompression-bomb
# guard exists for untrusted web images and would refuse every real sheet.
Image.MAX_IMAGE_PIXELS = None

log = logging.getLogger("sheetsplit")

SHEET_SUFFIXES = {".tif", ".tiff"}
LEDGER_NAME = "_index.json"
PREVIEW_DIRNAME = "_previews"


# ---------------------------------------------------------------- settings


@dataclass
class Settings:
    """Everything tunable. Lives in a JSON file so thresholds can be dialled in
    on real patterns without waiting for a new build."""

    output_root: str = ""
    margin_in: float = 0.125          # kept outside the black line, for the laser
    min_piece_in: float = 1.0         # longest side; smaller blobs are specks
    ink_threshold: int = 12           # 0-255; above this a pixel is ink, not media
    close_px: int = 2                 # bridges pinholes in an outline
    detect_max_px: int = 2000         # long edge of the detection copy
    cleanup_days: int = 14            # 0 disables
    default_dpi: float = 300.0        # only used if the TIFF doesn't say
    compression: str = "tiff_deflate"
    skip_existing: bool = True

    def __post_init__(self):
        if not self.output_root:
            self.output_root = str(default_output_root())

    # -- disk

    @staticmethod
    def path() -> Path:
        if sys.platform == "win32":
            base = Path(os.environ.get("APPDATA", Path.home())) / "SheetSplitter"
        else:
            base = Path.home() / ".config" / "sheetsplit"
        return base / "settings.json"

    @classmethod
    def load(cls) -> "Settings":
        try:
            data = json.loads(cls.path().read_text())
            known = {f for f in cls.__dataclass_fields__}
            return cls(**{k: v for k, v in data.items() if k in known})
        except Exception:
            return cls()

    def save(self) -> None:
        p = self.path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(asdict(self), indent=2))


def default_output_root() -> Path:
    """Local scratch, deliberately not beside the sheet: the sheets live in
    Synology Drive, and pieces written next to them would sync back up to the
    NAS -- roughly another sheet's worth of data per sheet, forever."""
    if sys.platform == "win32":
        return Path(os.environ.get("SystemDrive", "C:")) / os.sep / "Sheet Pieces"
    return Path.home() / "Sheet Pieces"


# ---------------------------------------------------------------- results


@dataclass
class Piece:
    index: int
    path: str
    box_px: tuple            # left, top, right, bottom in full-resolution pixels
    width_in: float
    height_in: float


@dataclass
class SheetResult:
    sheet: str
    ok: bool = False
    skipped: bool = False
    message: str = ""
    folder: str = ""
    preview: str = ""
    dpi: float = 0.0
    seconds: float = 0.0
    pieces: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    @property
    def name(self) -> str:
        return Path(self.sheet).stem

    @property
    def count(self) -> int:
        return len(self.pieces)


class Cancelled(Exception):
    pass


# ---------------------------------------------------------------- reading


def sheet_dpi(im: Image.Image, fallback: float) -> tuple:
    """TIFF resolution, or the fallback. Getting this wrong makes every piece
    print at the wrong physical size, so it is worth being fussy about."""
    dpi = im.info.get("dpi")
    if dpi:
        try:
            x, y = float(dpi[0]), float(dpi[1])
            if x > 1 and y > 1:
                return x, y
        except Exception:
            pass
    try:  # some writers only set the raw tags
        tags = im.tag_v2
        unit = tags.get(296, 2)  # 2 = inch, 3 = cm
        x, y = float(tags[282]), float(tags[283])
        if unit == 3:
            x, y = x * 2.54, y * 2.54
        if x > 1 and y > 1:
            return x, y
    except Exception:
        pass
    return fallback, fallback


def ink_map(small: Image.Image) -> np.ndarray:
    """How much ink is on each pixel, 0-255, whatever the colour space.

    Blank sublimation media is zero ink in CMYK, so this finds the printed piece
    as a whole rather than hunting specifically for the black line. The outline
    still matters -- it is what guarantees the piece reads as one closed blob
    even when the art inside runs pale."""
    arr = np.asarray(small)
    mode = small.mode
    if mode == "CMYK":
        return arr.max(axis=2)
    if mode in ("RGB", "RGBA"):
        return 255 - arr[:, :, :3].min(axis=2)
    if mode == "L":
        return 255 - arr
    return 255 - np.asarray(small.convert("L"))


# ---------------------------------------------------------------- detection


def _reading_order(boxes: list) -> list:
    """Top-left to bottom-right the way a person reads the sheet, so piece 7 on
    the preview is _07.tif in the folder. Rows are banded by height, otherwise a
    piece sitting fractionally higher than its neighbour jumps the queue."""
    if not boxes:
        return []
    heights = sorted(b[3] - b[1] for b in boxes)
    band = max(1, heights[len(heights) // 2] // 2)
    ordered = sorted(boxes, key=lambda b: (b[1] // band, b[0]))
    return ordered


def _drop_contained(boxes: list) -> list:
    """A piece's printed name or logo can sit inside the outline without touching
    the art, which makes it its own blob. Since piece boxes never overlap on
    these sheets, any box wholly inside another is something in a piece, not a
    piece."""
    keep = []
    for i, b in enumerate(boxes):
        inside = any(
            i != j
            and b[0] >= o[0] and b[1] >= o[1] and b[2] <= o[2] and b[3] <= o[3]
            and (o[2] - o[0]) * (o[3] - o[1]) > (b[2] - b[0]) * (b[3] - b[1])
            for j, o in enumerate(boxes)
        )
        if not inside:
            keep.append(b)
    return keep


def detect_pieces(im: Image.Image, s: Settings, dpi: tuple):
    """Return (boxes in full-resolution pixels, the small image detection ran on,
    the detection scale factor)."""
    factor = max(1, math.ceil(max(im.size) / max(200, s.detect_max_px)))
    small = im.reduce(factor) if factor > 1 else im.copy()

    mask = ink_map(small) > s.ink_threshold
    if s.close_px > 0:
        mask = ndimage.binary_closing(
            mask, structure=np.ones((3, 3), bool), iterations=s.close_px
        )

    labels, n = ndimage.label(mask, structure=np.ones((3, 3), bool))
    log.info("detection: %s blobs at 1/%s scale", n, factor)

    min_px = (s.min_piece_in * min(dpi)) / factor
    boxes = []
    for sl in ndimage.find_objects(labels):
        if sl is None:
            continue
        ys, xs = sl
        w, h = xs.stop - xs.start, ys.stop - ys.start
        if max(w, h) < min_px:
            continue
        boxes.append((xs.start, ys.start, xs.stop, ys.stop))

    boxes = _reading_order(_drop_contained(boxes))
    full = [
        (
            b[0] * factor,
            b[1] * factor,
            min(b[2] * factor, im.size[0]),
            min(b[3] * factor, im.size[1]),
        )
        for b in boxes
    ]
    return full, small, factor


# ---------------------------------------------------------------- preview


BRAND_PINK = (236, 72, 153)   # #ec4899, sampled from the logo; not re-pickable
NEAR_BLACK = (10, 10, 10)

FONT_CANDIDATES = (
    "arialbd.ttf", "segoeuib.ttf", "Arial Bold.ttf",           # Windows
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",       # macOS, for dev
    "/System/Library/Fonts/Helvetica.ttc",
    "DejaVuSans-Bold.ttf",
)


def load_font(size: int):
    for name in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size=size)   # Pillow >= 10.1
    except Exception:
        return ImageFont.load_default()


def build_preview(small: Image.Image, boxes: list, factor: int, out: Path) -> str:
    """The whole sheet with numbered boxes drawn on. This is the check, and it
    happens on screen -- nothing lands in the pieces folder to be tidied up."""
    canvas = small.convert("RGB")
    scale = max(1, min(2, 1400 // max(1, max(canvas.size))))
    if scale > 1:
        canvas = canvas.resize((canvas.size[0] * scale, canvas.size[1] * scale))
    draw = ImageDraw.Draw(canvas)
    line = max(2, canvas.size[0] // 400)
    size = max(14, canvas.size[0] // 45)
    font = load_font(size)

    for i, b in enumerate(boxes, 1):
        x0, y0 = b[0] / factor * scale, b[1] / factor * scale
        x1, y1 = b[2] / factor * scale, b[3] / factor * scale
        draw.rectangle([x0, y0, x1, y1], outline=BRAND_PINK, width=line)
        label = str(i)
        tw = draw.textlength(label, font=font)
        pad = size // 4
        draw.rectangle(
            [x0, y0, x0 + tw + pad * 2, y0 + size + pad * 2], fill=BRAND_PINK
        )
        # Near-black on the pink, never white: measured 5.35:1 against white's
        # 3.53:1. Same rule as the apps -- docs/design-guidelines.md.
        draw.text((x0 + pad, y0 + pad), label, fill=NEAR_BLACK, font=font)

    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out, "PNG")
    return str(out)


# ---------------------------------------------------------------- ledger


def _ledger_path(root: Path) -> Path:
    return root / LEDGER_NAME


def read_ledger(root: Path) -> dict:
    try:
        return json.loads(_ledger_path(root).read_text())
    except Exception:
        return {}


def write_ledger(root: Path, data: dict) -> None:
    root.mkdir(parents=True, exist_ok=True)
    try:
        _ledger_path(root).write_text(json.dumps(data, indent=2))
    except Exception as exc:  # never fail a split over bookkeeping
        log.warning("could not write ledger: %s", exc)


def cleanup(root: Path, days: int) -> int:
    """Delete piece folders older than `days`. Only ever touches folders this
    program recorded creating, under its own output root -- it cannot eat
    anything of the user's, even if the output root is set somewhere odd."""
    if days <= 0 or not root.exists():
        return 0
    ledger, cutoff, removed = read_ledger(root), time.time() - days * 86400, 0
    for key, entry in list(ledger.items()):
        if entry.get("finished", 0) >= cutoff:
            continue
        folder = Path(entry.get("folder", ""))
        try:
            if folder.exists() and folder.resolve().parent == root.resolve():
                shutil.rmtree(folder)
                removed += 1
            preview = Path(entry.get("preview", ""))
            if preview.exists() and preview.parent.name == PREVIEW_DIRNAME:
                preview.unlink()
        except Exception as exc:
            log.warning("cleanup skipped %s: %s", folder, exc)
            continue
        ledger.pop(key, None)
    if removed:
        write_ledger(root, ledger)
        log.info("cleanup removed %s folder(s) older than %s days", removed, days)
    return removed


def existing_split(root: Path, sheet: Path) -> dict | None:
    """Was this exact sheet already split, and is the result still on disk and
    still newer than the sheet? Saves re-downloading gigabytes from the NAS."""
    entry = read_ledger(root).get(str(sheet).lower())
    if not entry:
        return None
    folder = Path(entry.get("folder", ""))
    if not folder.exists() or not any(folder.glob("*.tif")):
        return None
    try:
        if sheet.stat().st_mtime > entry.get("finished", 0):
            return None
    except OSError:
        return None
    return entry


# ---------------------------------------------------------------- splitting


def unique_folder(root: Path, name: str) -> Path:
    folder = root / name
    n = 2
    while folder.exists() and any(folder.iterdir()):
        folder = root / f"{name} ({n})"
        n += 1
    return folder


def split_sheet(sheet: Path, s: Settings, on_step=None, cancel=None) -> SheetResult:
    """Split one sheet. `on_step(text, fraction)` drives the progress bar;
    `cancel()` returning True aborts between steps."""
    started = time.time()
    res = SheetResult(sheet=str(sheet))
    root = Path(s.output_root)

    def step(text, frac):
        if cancel and cancel():
            raise Cancelled()
        if on_step:
            on_step(text, frac)

    try:
        if s.skip_existing:
            prior = existing_split(root, sheet)
            if prior:
                res.ok, res.skipped = True, True
                res.folder, res.preview = prior.get("folder", ""), prior.get("preview", "")
                res.pieces = [Piece(**p) for p in prior.get("pieces", [])]
                res.dpi = prior.get("dpi", 0.0)
                res.message = "already split"
                res.seconds = time.time() - started
                return res

        # One decode, and one only. Everything downstream works from this copy:
        # cropping straight from the file would re-decompress the sheet per piece.
        step("reading sheet", 0.05)
        with Image.open(sheet) as opened:
            dpi = sheet_dpi(opened, s.default_dpi)
            icc = opened.info.get("icc_profile")
            im = opened
            im.load()

            if dpi == (s.default_dpi, s.default_dpi) and not opened.info.get("dpi"):
                res.warnings.append(
                    f"sheet has no resolution tag; assuming {s.default_dpi:g} dpi"
                )

            res.dpi = dpi[0]
            log.info("%s: %sx%s %s @ %.0f dpi", sheet.name, im.size[0], im.size[1],
                     im.mode, dpi[0])

            step("finding pieces", 0.35)
            boxes, small, factor = detect_pieces(im, s, dpi)
            if not boxes:
                res.message = "no pieces found"
                res.preview = build_preview(
                    small, [], factor, root / PREVIEW_DIRNAME / f"{sheet.stem}.png"
                )
                res.seconds = time.time() - started
                return res

            step(f"cutting {len(boxes)} pieces", 0.45)
            folder = unique_folder(root, sheet.stem)
            folder.mkdir(parents=True, exist_ok=True)
            pad = max(2, len(str(len(boxes))))
            mx, my = round(s.margin_in * dpi[0]), round(s.margin_in * dpi[1])

            for i, b in enumerate(boxes, 1):
                box = (
                    max(0, b[0] - mx),
                    max(0, b[1] - my),
                    min(im.size[0], b[2] + mx),
                    min(im.size[1], b[3] + my),
                )
                out = folder / f"{sheet.stem}_{i:0{pad}d}.tif"
                piece = im.crop(box)
                save_kwargs = dict(compression=s.compression, dpi=dpi)
                if icc:
                    save_kwargs["icc_profile"] = icc
                piece.save(out, "TIFF", **save_kwargs)
                piece.close()
                res.pieces.append(
                    Piece(
                        index=i,
                        path=str(out),
                        box_px=box,
                        width_in=round((box[2] - box[0]) / dpi[0], 3),
                        height_in=round((box[3] - box[1]) / dpi[1], 3),
                    )
                )
                step(f"cutting piece {i} of {len(boxes)}", 0.45 + 0.5 * i / len(boxes))

            step("drawing preview", 0.97)
            res.preview = build_preview(
                small, boxes, factor, root / PREVIEW_DIRNAME / f"{sheet.stem}.png"
            )

        res.folder = str(folder)
        res.ok = True
        res.seconds = time.time() - started

        ledger = read_ledger(root)
        ledger[str(sheet).lower()] = {
            "sheet": str(sheet),
            "folder": res.folder,
            "preview": res.preview,
            "dpi": res.dpi,
            "finished": time.time(),
            "pieces": [asdict(p) for p in res.pieces],
        }
        write_ledger(root, ledger)
        log.info("%s: %s pieces in %.1fs", sheet.name, res.count, res.seconds)
        return res

    except Cancelled:
        raise
    except Exception as exc:
        log.exception("failed on %s", sheet)
        res.message = f"{type(exc).__name__}: {exc}"
        res.seconds = time.time() - started
        return res


def flag_outliers(results: list) -> None:
    """Sizes of one pattern should give the same piece count. Eleven sheets at
    14 and one at 9 means two pieces touched and were read as one -- the failure
    most likely to actually happen, and free to catch once sheets are done as a
    batch."""
    counts = [r.count for r in results if r.ok and r.count]
    if len(counts) < 3:
        return
    common = max(set(counts), key=counts.count)
    if counts.count(common) < len(counts) * 0.6:
        return  # no clear norm; flagging would be noise
    for r in results:
        if r.ok and r.count and r.count != common:
            r.warnings.append(
                f"{r.count} pieces, but most sheets in this batch found {common}"
                " -- check the preview for two pieces read as one"
            )


def gather_sheets(paths) -> list:
    """Files as given; folders expanded to the TIFFs directly inside them."""
    out = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            out += sorted(
                c for c in p.iterdir()
                if c.is_file() and c.suffix.lower() in SHEET_SUFFIXES
            )
        elif p.is_file() and p.suffix.lower() in SHEET_SUFFIXES:
            out.append(p)
    seen, unique = set(), []
    for p in out:
        key = str(p).lower()
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def setup_logging(root: Path) -> None:
    try:
        root.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(root / "sheet-splitter.log", encoding="utf-8")
    except Exception:
        handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.setLevel(logging.INFO)
    log.handlers[:] = [handler]
