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
import platform
import shutil
import sys
import atexit
import tempfile
import time
import zipfile
from dataclasses import dataclass, asdict, field
from logging.handlers import RotatingFileHandler
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import __version__
from scipy import ndimage

# Print-resolution sheets are hundreds of megapixels. Pillow's decompression-bomb
# guard exists for untrusted web images and would refuse every real sheet.
Image.MAX_IMAGE_PIXELS = None

log = logging.getLogger("sheetsplit")

SHEET_SUFFIXES = {".tif", ".tiff"}
PREVIEW_DIRNAME = "_previews"


# ---------------------------------------------------------------- settings


@dataclass
class Settings:
    """Everything tunable. Lives in a JSON file so thresholds can be dialled in
    on real patterns without waiting for a new build."""

    settings_version: int = 3
    margin_in: float = 0.125          # kept outside the black line, for the laser
    min_piece_in: float = 1.0         # longest side; smaller blobs are specks
    ink_threshold: int = 12           # 0-255; above this a pixel is ink, not media
    close_px: int = 2                 # bridges pinholes in an outline
    detect_max_px: int = 2000         # long edge of the detection copy
    default_dpi: float = 300.0        # only used if the TIFF doesn't say
    compression: str = "tiff_deflate"
    skip_existing: bool = True
    # Where the pieces land. "beside" puts them next to the sheet they came
    # from; "folder" puts every sheet's pieces under dest_dir.
    dest_mode: str = "beside"
    dest_dir: str = ""
    text_scale: int = 100
    # Point this at a Synology-synced folder and a diagnostics zip lands
    # somewhere it can be read without anyone emailing a file around.
    diagnostics_dir: str = ""

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
            s = cls(**{k: v for k, v in data.items() if k in known})
            if data.get("settings_version", 1) < 3:
                s.settings_version = 3
                s.save()
            return s
        except Exception:
            return cls()

    def save(self) -> None:
        p = self.path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(asdict(self), indent=2))


def pieces_base(sheet: Path, s: Settings) -> Path:
    """The directory a sheet's piece folder is created in.

    "beside" puts them next to the sheet they came from, which is what people
    expect and means nothing has to be found later. ⚠ For sheets living in
    Synology Drive that does mean the pieces sync back up to the NAS.
    """
    if s.dest_mode == "beside":
        return sheet.parent
    return Path(s.dest_dir) if s.dest_dir else sheet.parent


def app_data_dir() -> Path:
    """The standard per-user spot for the settings file and the rolling log.

    Not a folder anyone has to look at or tidy up -- the point is that the app
    leaves nothing lying around in the places people actually work."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "SheetSplitter"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "SheetSplitter"
    else:
        base = Path.home() / ".local" / "share" / "sheetsplit"
    base.mkdir(parents=True, exist_ok=True)
    return base


def default_diagnostics_dir() -> Path:
    """Somewhere findable. A diagnostics zip only appears when it is asked for,
    so it goes where it can be picked up rather than into a hidden folder."""
    desktop = Path.home() / "Desktop"
    return desktop if desktop.is_dir() else Path.home()


_WORK_DIR = None


def work_dir() -> Path:
    """Scratch for previews and ink masks, in the system temp area and thrown
    away when the app closes. Nothing here outlives a session."""
    global _WORK_DIR
    if _WORK_DIR is None or not Path(_WORK_DIR).exists():
        _WORK_DIR = Path(tempfile.mkdtemp(prefix="sheetsplit-"))
    return Path(_WORK_DIR)


def discard_work_dir() -> None:
    global _WORK_DIR
    if _WORK_DIR:
        shutil.rmtree(_WORK_DIR, ignore_errors=True)
        _WORK_DIR = None


def sweep_old_work_dirs(hours: int = 12) -> int:
    """Clear scratch left by a session that was killed rather than closed.

    Only ever touches directories this program named, inside the system temp
    area -- so it cannot reach anything of anyone else's."""
    removed, cutoff = 0, time.time() - hours * 3600
    try:
        for path in Path(tempfile.gettempdir()).glob("sheetsplit-*"):
            if path.is_dir() and path.stat().st_mtime < cutoff:
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
    except Exception as exc:
        log.warning("could not sweep old scratch: %s", exc)
    return removed


# Whatever ends the process -- closing the window, finishing a command, an
# unhandled error -- the scratch goes with it.
atexit.register(discard_work_dir)


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

    min_px = (s.min_piece_in * min(dpi)) / factor
    boxes, too_small = [], 0
    for sl in ndimage.find_objects(labels):
        if sl is None:
            continue
        ys, xs = sl
        w, h = xs.stop - xs.start, ys.stop - ys.start
        if max(w, h) < min_px:
            too_small += 1
            continue
        boxes.append((xs.start, ys.start, xs.stop, ys.stop))

    kept = _drop_contained(boxes)
    # Logged in detail because this is the first thing to look at when a split
    # comes out wrong, and the machine that ran it is not the one I can see.
    log.info("detection at 1/%s scale: %s blobs, %s under %.2fin, %s inside "
             "another piece, %s pieces", factor, n, too_small, s.min_piece_in,
             len(boxes) - len(kept), len(kept))
    boxes = _reading_order(kept)
    full = [
        (
            b[0] * factor,
            b[1] * factor,
            min(b[2] * factor, im.size[0]),
            min(b[3] * factor, im.size[1]),
        )
        for b in boxes
    ]
    return full, small, factor, mask


def mask_path(root: Path, sheet: str) -> Path:
    return root / PREVIEW_DIRNAME / f"{Path(sheet).stem}-mask.png"


def save_mask(mask: np.ndarray, out: Path) -> str:
    """What the splitter thought was ink. Reading this next to the preview is
    how a threshold problem gets diagnosed from another country."""
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        img = Image.fromarray(np.where(mask, 0, 255).astype(np.uint8), "L")
        img.thumbnail((1200, 1200), Image.NEAREST)
        img.save(out, "PNG", optimize=True)
        return str(out)
    except Exception as exc:
        log.warning("could not save mask: %s", exc)
        return ""


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


# ------------------------------------------------------- already split?


def already_split(sheet: Path, s: Settings) -> Path | None:
    """Has this sheet already been split, and is the result still newer than it?

    Asks the filesystem rather than keeping an index of its own. One less file
    to leave behind, and it cannot disagree with what is actually on disk.
    """
    folder = pieces_base(sheet, s) / f"{sheet.stem} pieces"
    if not folder.is_dir() or not any(folder.glob("*.tif")):
        return None
    try:
        newest = max(p.stat().st_mtime for p in folder.glob("*.tif"))
        if sheet.stat().st_mtime > newest:
            return None
    except OSError:
        return None
    return folder


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

    def step(text, frac):
        if cancel and cancel():
            raise Cancelled()
        if on_step:
            on_step(text, frac)

    try:
        prior = already_split(sheet, s)
        if prior and not s.skip_existing:
            # Deliberately splitting it again: replace the old folder rather than
            # leaving a "(2)" beside it for someone to pick the wrong one from.
            old = prior
            try:
                if old.exists() and old.name.startswith(sheet.stem):
                    shutil.rmtree(old)
            except Exception as exc:
                log.warning("could not replace %s: %s", old, exc)
        if s.skip_existing and prior:
            res.ok, res.skipped = True, True
            res.folder = str(prior)
            res.message = f"already split — {len(list(prior.glob('*.tif')))} pieces"
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
            try:
                size_mb = sheet.stat().st_size / 1e6
            except OSError:
                size_mb = 0
            log.info("%s: %sx%s %s @ %.0f dpi, %s on disk %.0fMB, read in %.1fs",
                     sheet.name, im.size[0], im.size[1], im.mode, dpi[0],
                     opened.info.get("compression", "?"), size_mb,
                     time.time() - started)

            step("finding pieces", 0.35)
            boxes, small, factor, mask = detect_pieces(im, s, dpi)
            save_mask(mask, mask_path(work_dir(), str(sheet)))
            if not boxes:
                res.message = "no pieces found"
                res.preview = build_preview(
                    small, [], factor, work_dir() / PREVIEW_DIRNAME / f"{sheet.stem}.png"
                )
                res.seconds = time.time() - started
                return res

            step(f"cutting {len(boxes)} pieces", 0.45)
            folder = unique_folder(pieces_base(sheet, s), f"{sheet.stem} pieces")
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
                small, boxes, factor, work_dir() / PREVIEW_DIRNAME / f"{sheet.stem}.png"
            )

        res.folder = str(folder)
        res.ok = True
        res.seconds = time.time() - started

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


def setup_logging(root: Path = None) -> None:
    """Rotating, so it survives months of use without becoming a 400MB file
    nobody can send anywhere."""
    try:
        root = Path(root) if root else app_data_dir()
        root.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(root / "sheet-splitter.log", maxBytes=2_000_000,
                                      backupCount=3, encoding="utf-8")
    except Exception:
        handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.setLevel(logging.INFO)
    log.handlers[:] = [handler]
    log.info("--- Sheet Splitter %s started ---", __version__)
    for line in system_report():
        log.info("  %s", line)


def system_report() -> list:
    """Everything I'd otherwise have to ask about over chat."""
    lines = [
        f"version {__version__}",
        f"python {sys.version.split()[0]} ({'frozen exe' if getattr(sys, 'frozen', False) else 'source'})",
        f"platform {platform.platform()}",
        f"machine {platform.machine()}, cpus {os.cpu_count()}",
    ]
    try:
        import numpy
        import scipy
        lines.append(f"pillow {Image.__version__}, numpy {numpy.__version__}, "
                     f"scipy {scipy.__version__}")
    except Exception:
        pass
    try:  # free space matters: sheets hydrate from the NAS onto a small SSD
        usage = shutil.disk_usage(Path.home().anchor)
        lines.append(f"disk free {usage.free / 1e9:.0f}GB of {usage.total / 1e9:.0f}GB")
    except Exception:
        pass
    if sys.platform == "win32":
        try:
            import ctypes

            class MemStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong),
                            ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            status = MemStatus()
            status.dwLength = ctypes.sizeof(MemStatus)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
            lines.append(f"ram {status.ullTotalPhys / 1e9:.0f}GB total, "
                         f"{status.ullAvailPhys / 1e9:.0f}GB free")
        except Exception:
            pass
    return lines


def build_diagnostics(results: list, s: Settings, stamp: str,
                      into: Path | None = None) -> Path:
    """One zip to send when something looks wrong.

    Deliberately holds the small things that explain a bad split -- the log, the
    settings, the numbered preview and the ink mask detection actually worked
    from -- and never the sheet itself, which is gigabytes.
    """
    target_dir = into or Path(s.diagnostics_dir or default_diagnostics_dir())
    target_dir.mkdir(parents=True, exist_ok=True)
    out = target_dir / f"sheet-splitter-diagnostics-{stamp}.zip"

    summary = {
        "when": stamp,
        "system": system_report(),
        "settings": asdict(s),
        "sheets": [
            {
                "sheet": r.sheet,
                "ok": r.ok,
                "skipped": r.skipped,
                "message": r.message,
                "dpi": r.dpi,
                "seconds": round(r.seconds, 2),
                "warnings": r.warnings,
                "piece_count": r.count,
                "pieces": [asdict(p) for p in r.pieces],
            }
            for r in results
        ],
    }

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("summary.json", json.dumps(summary, indent=2))
        for name in ("sheet-splitter.log", "sheet-splitter.log.1"):
            p = app_data_dir() / name
            if p.exists():
                z.write(p, name)
        for r in results:  # the pictures that explain a bad split
            for path, label in ((r.preview, "preview"), (mask_path(work_dir(), r.sheet), "mask")):
                p = Path(path) if path else None
                if p and p.exists():
                    z.write(p, f"{label}/{p.name}")
    log.info("diagnostics written to %s", out)
    return out
