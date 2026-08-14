# Sheet Splitter

Splits a nested sublimation cut-and-sew sheet into one file per piece, ready to
load straight into Epson Edge Print — instead of cropping and copying every
piece by hand for every pattern and every size.

Each piece is found by the thick black laser line drawn round it. Crops keep
DPI, CMYK and the colour profile intact, so a piece prints at exactly the size
it does today.

The design and the reasoning behind it: **[PLAN.md](PLAN.md)**.

## Using it on the print PC

Put `Sheet Splitter.exe` on the desktop. Nothing to install.

1. Double-click it.
2. **Choose sheets…** (several at once is fine) or **Choose a folder…** for
   every TIFF in one go. Dragging sheets onto the icon works too.
3. Watch the numbered preview. **Check the piece count** before printing —
   that is what the preview is for.
4. **Open folder**, select all, drag into Edge Print.

Pieces are written to `C:\Sheet Pieces\<sheet name>\`, deliberately not next to
the sheet: the sheets live in Synology Drive, so pieces written beside them
would sync back up to the NAS. They are deleted after 14 days, and regenerate
in seconds from a sheet you still have.

Windows will warn once that it doesn't recognise the app — **More info → Run
anyway**. That is what an unsigned exe looks like; a signing certificate costs a
few hundred a year and isn't worth it for an in-shop tool.

### When a split looks wrong

The window flags a sheet whose piece count differs from the rest of the batch —
usually two pieces close enough to be read as one.

Everything worth adjusting is under **Settings**:

| Setting | Try this when |
|---|---|
| Ink threshold | Blank media reads as ink (whole sheet found as one piece) — raise it. A pale piece is missed — lower it. |
| Smallest piece | Registration marks becoming files — raise it. A small piece missed — lower it. |
| Margin outside the line | Cut line looks tight against the edge of a piece. |
| Delete pieces after | 0 keeps them forever. |

There's a log at `C:\Sheet Pieces\sheet-splitter.log`.

## Running it from a command line

```
python -m sheetsplit.cli sheet.tif [more.tif ...] [--out DIR] [--force]
```

Same splitter, no window — for batches, scripting, or working out what a
setting does.

## Developing

```
pip install -r requirements.txt
python tools/make_test_sheet.py /tmp/sheet.tif --inches 24x36 --pieces 12
python -m sheetsplit.cli /tmp/sheet.tif --out /tmp/out
python -m sheetsplit.gui
```

`tools/make_test_sheet.py` builds a synthetic sheet that stands in for a real
export: CMYK, zip-compressed, black-outlined pieces, plus the two things that
must *not* become files — registration specks, and labels printed inside a
piece.

Pushing to `main` builds the Windows exe on a Windows runner, splits a synthetic
sheet to prove the exe works, and photographs the running window. All three land
in the run's artifacts.

- `sheetsplit/core.py` — finding pieces, cropping, the ledger and cleanup
- `sheetsplit/gui.py` — the window
- `sheetsplit/theme.py` — the Big Tee surface. ⚠ Never white ink on the brand
  pink; the rules live in the job tracker's `docs/design-guidelines.md`.
