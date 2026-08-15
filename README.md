# Sheet Splitter

Splits a nested sublimation cut-and-sew sheet into one file per piece, ready to
load straight into Epson Edge Print — instead of cropping and copying every
piece by hand for every pattern and every size.

Pieces are found by looking for **printed ink against blank media**, so it works
whether or not a piece carries a cut line round it. Crops keep DPI, CMYK and the
colour profile intact, so a piece prints at exactly the size it does today.

Real numbers, from a 2GB jersey sheet (21569 × 15173, 8 pieces): preview on
screen in **0.6s**, all pieces written in **7.5s**, peak memory **2.2GB**.

The design and the reasoning behind it: **[PLAN.md](PLAN.md)**.

## Using it

Runs on the Mac at the print station. Put `Sheet Splitter.app` in Applications
or on the desktop — nothing to install.

1. Open it.
2. **Add sheets…**, **Add a folder…**, or drag sheets onto the list.
   Each one is read and previewed straight away — under a second on a 2GB
   sheet — so the numbered preview and the piece count are there before you
   commit to anything.
3. **Check the preview and the piece count.** This is the point of the tool: a
   wrong count is far cheaper to spot here than on printed media.
4. **Choose destination…** — where every sheet's pieces are written.
5. Press **Start**. (If you haven't set a destination it asks for one and then
   carries on.)
6. A dialog says **Complete**, with `sheet.tif — 8 of 8 pieces cut` for each
   sheet, or **Finished with errors** listing what failed.
7. **Open folder**, select all, drag into Edge Print.

Each sheet gets its own `<name> pieces` folder inside the destination.

⚠ **The destination is never remembered between sessions.** That is deliberate:
having a default is how a batch ends up somewhere nobody thought about.

**It leaves nothing behind.** The pieces are the only thing written where you
can see them. Previews and ink masks are temporary and go when the window
closes — including after a crash, since it clears its own leftovers on the next
launch. Settings and a small rolling log live in
`~/Library/Application Support/SheetSplitter`. Nothing is ever auto-deleted.

⚠ A copy downloaded from the releases page is quarantined by macOS, which
reports it as damaged rather than unsigned. Clear it once:

    xattr -dr com.apple.quarantine "/Applications/Sheet Splitter.app"

A copy handed over locally rather than downloaded doesn't carry the flag.

### When a split looks wrong

The end-of-run dialog flags a sheet whose piece count differs from the rest of
the batch — usually two pieces close enough to be read as one.

Everything worth adjusting is under **Settings** in the menu bar (⌘,):

| Setting | Try this when |
|---|---|
| Ink threshold | Blank media reads as ink (whole sheet found as one piece) — raise it. A pale piece is missed — lower it. |
| Smallest piece | Registration marks becoming files — raise it. A small piece missed — lower it. |
| Margin outside the line | The crop looks tight against the edge of a piece. |
| Text size | 130 makes everything noticeably bigger. Applies next time you open it. |

### When it needs looking at from another machine

**Save diagnostics** (menu bar → Settings) writes one zip holding the log, the
current settings, and for each sheet its numbered preview *and the ink mask the
splitter actually worked from* — usually the thing that explains a bad split. It
never includes the sheets themselves. It lands on the Desktop unless *Send
diagnostics to* says otherwise.

The rolling log records the version and build, the machine, free disk, each
sheet's dimensions and DPI, and for every sheet how many blobs were found, how
many were dropped for being too small, how many were inside another piece, and
how many became files. **Every build stamps its commit into that log** — an
earlier run of versions all reported `1.0.0`, and not knowing which build a
report came from cost real time.

## Running it from a command line

```
python -m sheetsplit.cli sheet.tif [more.tif ...] --out DIR
```

Same splitter, no window — for batches, scripting, or working out what a
setting does.

The app takes `--start` (split immediately rather than waiting for the button)
and `--dest DIR`. That is how the build proves the packaged app really splits a
sheet.

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

Pushing to `main` builds the app on a macOS runner, makes it split a synthetic
sheet to prove the packaged bundle actually works, and photographs the running
window. All three land in the run's artifacts.

⚠ **The build must prove the app runs, not just that it compiled.** Two green
builds once shipped an app that died on launch, and a third opened with no
window at all — a windowed Mac app has nowhere to print a crash, so it simply
vanishes. The run step now fails unless the app is alive and has written checked
pieces, and any crash during startup is written to the log.

- `sheetsplit/core.py` — reading, finding pieces, cropping, diagnostics
- `sheetsplit/gui.py` — the window
- `sheetsplit/theme.py` — the Big Tee surface. ⚠ Never white ink on the brand
  pink; the rules live in the job tracker's `docs/design-guidelines.md`.
  Buttons are drawn as images because Tk's canvas has no antialiasing and
  rasterises a small radius into a 45° chamfer.

The Windows build was dropped on 2026-08-15 when the workflow moved to a Mac.
The Windows-specific code (DPI awareness, dark title bar, Explorer) is still in
place and inert, so bringing it back is a config change rather than a rewrite.
