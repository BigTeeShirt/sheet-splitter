"""Headless splitter -- used for testing, batch scripting and the CI smoke test.

    python3 -m sheetsplit.cli sheet.tif [more.tif ...] [--out DIR] [--force]
"""

import argparse
import sys
from pathlib import Path

from . import core


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="sheetsplit", description=__doc__)
    ap.add_argument("paths", nargs="+", help="sheet TIFFs, or folders of them")
    ap.add_argument("--out", help="output root (default: local scratch folder)")
    ap.add_argument("--margin", type=float, help="inches kept outside the black line")
    ap.add_argument("--min-piece", type=float, help="inches; smaller blobs are ignored")
    ap.add_argument("--threshold", type=int, help="0-255 ink threshold")
    ap.add_argument("--force", action="store_true", help="re-split sheets already done")
    ap.add_argument("--no-cleanup", action="store_true")
    args = ap.parse_args(argv)

    s = core.Settings.load()
    if args.out:
        s.output_root = str(Path(args.out).expanduser().resolve())
    if args.margin is not None:
        s.margin_in = args.margin
    if args.min_piece is not None:
        s.min_piece_in = args.min_piece
    if args.threshold is not None:
        s.ink_threshold = args.threshold
    if args.force:
        s.skip_existing = False

    root = Path(s.output_root)
    core.setup_logging(root)
    if not args.no_cleanup:
        core.cleanup(root, s.cleanup_days)

    sheets = core.gather_sheets(args.paths)
    if not sheets:
        print("no TIFF sheets found in those paths", file=sys.stderr)
        return 2

    results = []
    for n, sheet in enumerate(sheets, 1):
        print(f"[{n}/{len(sheets)}] {sheet.name} ... ", end="", flush=True)
        r = core.split_sheet(sheet, s)
        results.append(r)
        if r.skipped:
            print(f"already split, {r.count} pieces")
        elif r.ok:
            print(f"{r.count} pieces in {r.seconds:.1f}s -> {r.folder}")
        else:
            print(f"FAILED: {r.message}")

    core.flag_outliers(results)
    for r in results:
        for w in r.warnings:
            print(f"  ! {r.name}: {w}", file=sys.stderr)

    failed = [r for r in results if not r.ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} sheets split, "
          f"{sum(r.count for r in results)} pieces total")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
