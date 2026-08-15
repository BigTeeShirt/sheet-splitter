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
2. **Add sheets…** (several at once is fine) or **Add a folder…** for every TIFF
   in one go. Dragging sheets onto the icon adds them too. Nothing runs yet.
3. Press **Start**.
4. Watch the numbered preview. **Check the piece count** before printing —
   that is what the preview is for.
5. **Open folder**, select all, drag into Edge Print.

By default each sheet gets a **`<name> pieces` folder right next to it**, so
there is nothing to go looking for. Untick *Put pieces beside each sheet* and
**Choose destination…** to send them all to one folder instead.

⚠ Sheets living in Synology Drive means pieces written beside them **sync back
up to the NAS** — roughly another sheet's worth per sheet. If that becomes a
problem, point the destination at a local folder; pieces regenerate in seconds
from a sheet you still have.

⚠ The 14-day cleanup **only ever applies to a fixed destination**. Pieces
written beside a sheet are in your own folders and are never deleted.

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
| Delete pieces after | 0 keeps them forever. Never touches pieces beside a sheet. |
| Text size | 130 makes everything noticeably bigger. Applies next time you open it. |

### When it needs looking at from somewhere else

**Save diagnostics** writes one zip holding the log, the current settings, and
for each sheet in the batch its numbered preview *and the ink mask the splitter
actually worked from* — which is usually the thing that explains a bad split.
It never includes the sheets themselves.

Set **Send diagnostics to** in Settings to a Synology-synced folder and the zip
lands somewhere it can be read without anyone emailing a file around.

The rolling log lives at `C:\Sheet Pieces\sheet-splitter.log`. It records the
app version, the machine, free disk, each sheet's dimensions, colour mode, DPI
and compression, and for every sheet how many blobs were found, how many were
dropped for being too small, how many were inside another piece, and how many
became files.

## On a Mac

`Sheet Splitter.app` does the same job. Pieces go to `~/Sheet Pieces/`.

⚠ **A copy downloaded from the releases page is quarantined by macOS** and will
say it's damaged rather than that it's unsigned. Clear it once:

    xattr -dr com.apple.quarantine "/Applications/Sheet Splitter.app"

A copy handed over locally rather than downloaded doesn't carry the flag and
just opens.

## Running it from a command line

```
python -m sheetsplit.cli sheet.tif [more.tif ...] [--out DIR] [--force]
```

Same splitter, no window — for batches, scripting, or working out what a
setting does. `--out DIR` puts every sheet's pieces under one folder instead of
beside each sheet.

The app itself takes `--start`, which splits whatever it was given straight away
rather than waiting for the button. That is what the build uses to prove the
packaged app really splits a sheet.

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
