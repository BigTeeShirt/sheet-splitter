"""Assert a split folder holds what it should. Used by CI as the smoke test.

    python tools/check_split.py out/Synthetic-Pattern-M 12
"""

import sys
from pathlib import Path

from PIL import Image

Image.MAX_IMAGE_PIXELS = None


def main() -> int:
    folder, expected = Path(sys.argv[1]), int(sys.argv[2])
    pieces = sorted(folder.glob("*.tif"))
    problems = []

    if len(pieces) != expected:
        problems.append(f"expected {expected} pieces, found {len(pieces)}")
    if any(p.suffix.lower() not in {".tif", ".tiff"} for p in folder.iterdir()):
        problems.append("folder contains something that is not a piece TIFF")

    for p in pieces:
        with Image.open(p) as im:
            if im.mode != "CMYK":
                problems.append(f"{p.name} is {im.mode}, not CMYK")
            dpi = im.info.get("dpi")
            if not dpi or round(dpi[0]) != 300:
                problems.append(f"{p.name} has dpi {dpi}, expected 300")
            if min(im.size) < 50:
                problems.append(f"{p.name} is {im.size}, suspiciously small")

    for line in problems:
        print("FAIL:", line)
    if problems:
        return 1
    print(f"ok: {len(pieces)} pieces, all CMYK at 300dpi")
    return 0


if __name__ == "__main__":
    sys.exit(main())
