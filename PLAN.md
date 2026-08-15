# Sheet Splitter — plan

Agreed 2026-08-14, built 2026-08-15, and **in production use on real jerseys the
same day**.

⚠ **This document is the original plan, kept for the reasoning behind each
decision.** Several of them were overturned once it met real files and a real
operator. Where the two disagree, the README and the code are right:

| Planned | What actually happened |
|---|---|
| Windows PCs at the printers | **Mac only.** The workflow moved to a Mac Mini on day one. |
| Pieces found by the thick black cut line | ⚠ **Real sheets have no such line.** Detection finds printed ink against blank media, which works either way. |
| Pieces to local scratch, 14-day cleanup | **A destination must be chosen every session**, and nothing is ever auto-deleted. |
| Click and select, run on choose | **Explicit Start**, with previews the moment a sheet is added. |
| Pillow reading the sheet | **Parallel strip decode.** Pillow used one core: 7.3s of a 9.7s wait. Now 0.3s. |
| ~20–40s a sheet | **7.5s** for a 2GB, 8-piece jersey sheet. |

## The problem

Sublimation cut-and-sew exports come out as one big nested sheet with every part
on it. Epson Edge Print wants each part as its own file so it can step-and-repeat
that part per size, so today someone crops and copies each piece by hand — every
pattern, every size. Every piece carries a thick black outline used for visual
laser cutting; that outline is what makes the split automatable.

## Facts about the files (from Anthony)

- Flat **CMYK TIFF**, **zip (deflate) compressed**, 500MB–2GB, never over 2GB
  (so classic TIFF, no BigTIFF).
- **One size per sheet.**
- Art **stops at the black line** — no bleed past it.
- Piece boxes **never overlap** and the black line has **consistent thickness**.
- Sheets live on the **Synology**, synced to the print PC by Synology Drive
  **on-demand** — selecting a file in Edge Print is what triggers its download.

## Hardware it runs on

Windows PCs at the printers. 10th-gen i3, 16GB RAM, DRAM-less NVMe SSD,
bare-bones. **No GPU** — nothing here is GPU-shaped work; it's decompress,
threshold, copy rectangles. Cores and disk are what matter, and both are fine.

## How the split works

1. Decompress the sheet **once** into memory. (Naive per-piece cropping would
   re-decompress the source for every piece — twenty pieces, twenty
   decompressions. One pass is the whole performance story.)
2. Threshold a downscaled copy to find the black lines; consistent line
   thickness means a fixed rule, not per-file guessing. Close pinholes.
3. Label each closed outline as one piece, fill its interior, take its box.
   Drop anything under a minimum size so registration marks and specks never
   become files.
4. Map boxes back to full resolution, cut from the in-memory copy with a **1/8"
   margin outside the line** (art stops at the line; the full black outline must
   survive for the laser).
5. Write zip-compressed TIFFs with **DPI, CMYK and ICC profile intact** — pieces
   must print at exactly the size they do today.

Non-overlapping boxes means neighbour-masking isn't needed; keep it as a safety
net only.

## The app

Single **`.exe`** on the print PC's desktop. No Python install, no dependencies,
nothing to maintain on that machine.

- **Click and choose** — several sheets in the dialog, or a whole folder.
  Drag-onto-icon also works, but click-and-select is the one staff are taught.
- Works through sheets **one at a time** (each is gigabytes decompressed;
  parallel would just thrash). Can download the next while splitting the current.
- A failed file is marked and the batch **carries on**.
- **"Skip sheets already split"** so a folder can be re-run after adding files.
- Progress, then a per-sheet result list. Click a row for its **numbered
  preview** of the whole sheet — the check happens on screen, no report files
  on disk.
- **Odd-one-out check:** sizes of the same pattern should give the same piece
  count. Eleven sheets at 14 and one at 9 gets flagged — almost certainly two
  pieces touching and read as one. Free once it's doing them as a batch, and
  it's the failure most likely to actually happen.

## Output

**Local scratch, not the Synology.** Writing beside the sheet would sync every
piece back up to the NAS — roughly another sheet's worth per sheet, forever.
Pieces regenerate in ~30s, so they're scratch.

```
C:\Sheet Pieces\Deluzion-Jersey-L\
    Deluzion-Jersey-L_01.tif
    ...
    Deluzion-Jersey-L_14.tif
```

- **Nothing but piece TIFFs in the folder** — deliberate, so Ctrl+A and drag
  into Edge Print can't pick up a preview or a text file.
- Numbered **top-left to bottom-right**, so piece 7 on the preview is `_07.tif`.
- **Auto-cleanup at 14 days** (one setting), run on launch, and only ever on
  folders it created.
- Re-running a sheet still within the window offers to **open the existing
  folder** rather than re-downloading 2GB and redoing the work.
- Source sheet never touched.

## Build and delivery

Python + **Pillow, numpy and scipy**, packaged with PyInstaller into one exe by
a **GitHub Actions Windows runner** — built on real Windows, not cross-guessed
from a Mac. Download from the repo's releases page.

libvips was the plan and was dropped: it has no usable install on this Mac and
would have meant wrestling a native library into the bundle. With 16GB and a
2GB ceiling on sheets, decompressing once into memory is comfortable —
**measured 1.9GB peak for a 435-megapixel sheet** — and it removed the only
packaging risk that had no upside.

⚠ **The build must prove the exe runs, not just that it built.** The first two
green builds shipped an exe that died on launch with "Importing the numpy
C-extensions failed" — PyInstaller misses numpy's and scipy's compiled
submodules and says nothing, so `--collect-all` is load-bearing. CI now starts
the real exe on the runner's desktop, photographs the window, and fails unless
the app is still alive and has written twelve checked pieces. That screenshot
is also the only way to look at a Windows UI from a Mac.

**SmartScreen will warn once** on an unsigned exe ("More info → Run anyway").
Signing costs a few hundred a year; not worth it for an in-shop tool.

Expected: **20–40s a sheet** of actual work, plus Synology download time on
first touch, which will usually dominate.

## Deliberately not doing

- **No GPU.** Would sit idle.
- **No label reading yet** — plain numbering. Reading FRONT/BACK/SLEEVE off the
  sheet into filenames is a later add, once it's earned its keep.
- **No watched folder yet** — Anthony wants it eventually but needs to think the
  workflow through. Same tool, different trigger; today's build doesn't block it.
  The parts that decide it: which machine hosts the folder; three folders
  (in/out/done) not one; nobody's watching a screen so it needs a `failed`
  folder and notifications; the odd-one-out check weakens when sheets arrive one
  at a time; and a 2GB file appears in a folder long before it finishes writing,
  so the watcher must wait for it to stop growing.

## Open / to verify on the real machine

- **Does the read trigger a proper Synology Drive hydrate**, rather than being
  handed a 0-byte placeholder? Should be transparent, same as it is for Edge
  Print, but it's a test-on-the-box item.
- Tell Synology it can **free the sheet again** after splitting, so batch runs
  don't quietly fill a small drive.
- Actual decompressed size of a real sheet, to confirm the one-pass in-memory
  approach is comfortable in 16GB.
- **Touching pieces** merging into one blob is the expected failure mode; needs a
  real sheet to see whether it happens.
