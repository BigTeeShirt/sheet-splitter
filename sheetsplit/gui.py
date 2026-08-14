"""Sheet Splitter -- the window the print PC runs.

Pick sheets (or a folder), watch it work, then check the numbered preview before
sending anything to Edge Print. The check is the point: a wrong split is far
cheaper to spot here than on printed media.
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import traceback
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from . import core
from . import theme
from .theme import Button

APP_NAME = "Sheet Splitter"
WINDOW_TITLE = "Sheet Splitter — Big Tee Shirt Co."


def resource(name: str) -> Path:
    """Assets live beside the code in dev and inside the bundle once frozen."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    candidate = base / "assets" / name
    if candidate.exists():
        return candidate
    return Path(__file__).resolve().parent / "assets" / name


def _enable_dpi_awareness() -> None:
    """Without this the window is a blurry upscale on any scaled Windows
    display, which is most of them."""
    if sys.platform != "win32":
        return
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def open_in_explorer(path: str) -> None:
    if not path:
        return
    try:
        if sys.platform == "win32":
            os.startfile(path)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.run(["open", path], check=False)
        else:
            subprocess.run(["xdg-open", path], check=False)
    except Exception as exc:
        messagebox.showerror(APP_NAME, f"Could not open:\n{path}\n\n{exc}")


class SettingsDialog(tk.Toplevel):
    """The detection knobs, editable here so a stubborn pattern can be dialled
    in without waiting on a new build."""

    FIELDS = [
        ("output_root", "Pieces folder", "where the split files are written"),
        ("margin_in", "Margin outside the line (in)", "keeps the whole cut line"),
        ("min_piece_in", "Smallest piece (in)", "anything shorter is ignored"),
        ("ink_threshold", "Ink threshold (0–255)",
         "raise it if blank media is reading as ink"),
        ("cleanup_days", "Delete pieces after (days)", "0 keeps them forever"),
    ]

    def __init__(self, parent, settings: core.Settings, on_save):
        super().__init__(parent, bg=theme.WINDOW)
        self.title("Settings")
        self.settings, self.on_save = settings, on_save
        self.transient(parent)
        self.resizable(False, False)
        self.vars = {}

        frame = ttk.Frame(self, padding=20)
        frame.grid(sticky="nsew")
        ttk.Label(frame, text="Settings", style="Title.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 12))

        for i, (key, label, hint) in enumerate(self.FIELDS):
            row = 1 + i * 2
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w",
                                              pady=(10, 0))
            var = tk.StringVar(value=str(getattr(settings, key)))
            self.vars[key] = var
            ttk.Entry(frame, textvariable=var, width=42).grid(
                row=row, column=1, sticky="ew", padx=(14, 0), pady=(10, 0))
            if key == "output_root":
                Button(frame, "Browse", self._browse, pad=(12, 6)).grid(
                    row=row, column=2, padx=(8, 0), pady=(10, 0))
            ttk.Label(frame, text=hint, style="Muted.TLabel").grid(
                row=row + 1, column=1, sticky="w", padx=(14, 0))

        buttons = ttk.Frame(frame)
        buttons.grid(row=99, column=0, columnspan=3, sticky="e", pady=(22, 0))
        Button(buttons, "Save", self._save, variant="primary").pack(
            side="right", padx=(10, 0))
        Button(buttons, "Cancel", self.destroy).pack(side="right")

        self.grab_set()

    def _browse(self):
        chosen = filedialog.askdirectory(title="Where should pieces be written?")
        if chosen:
            self.vars["output_root"].set(chosen)

    def _save(self):
        try:
            s = self.settings
            s.output_root = self.vars["output_root"].get().strip() or s.output_root
            s.margin_in = max(0.0, float(self.vars["margin_in"].get()))
            s.min_piece_in = max(0.05, float(self.vars["min_piece_in"].get()))
            s.ink_threshold = min(254, max(0, int(float(self.vars["ink_threshold"].get()))))
            s.cleanup_days = max(0, int(float(self.vars["cleanup_days"].get())))
        except ValueError:
            messagebox.showerror(APP_NAME, "Those need to be numbers.", parent=self)
            return
        s.save()
        self.on_save()
        self.destroy()


class App(ttk.Frame):
    def __init__(self, master, argv_paths):
        super().__init__(master, padding=(18, 14, 18, 16))
        self.master = master
        self.settings = core.Settings.load()
        core.setup_logging(Path(self.settings.output_root))

        self.results: list = []
        self.events: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.cancel_flag = threading.Event()
        self._preview_img = None
        self._preview_path = ""
        self._resize_job = None
        self._logo = None

        self._build()
        self.pack(fill="both", expand=True)
        self.after(50, self._pump)

        removed = core.cleanup(Path(self.settings.output_root),
                               self.settings.cleanup_days)
        if removed:
            self._set_status(f"Tidied up {removed} old piece folder(s).")

        if argv_paths:  # sheets dragged onto the exe
            self.start(core.gather_sheets(argv_paths))

    # ---------------------------------------------------------------- layout

    def _build(self):
        self.master.title(WINDOW_TITLE)
        self.master.configure(bg=theme.WINDOW)
        theme.apply(self.master)
        try:
            self.master.iconphoto(True, tk.PhotoImage(file=resource("icon.png")))
        except Exception:
            pass

        self._build_header()

        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x", pady=(14, 0))
        # Pink is the ONE main action. Everything else is the bordered base button.
        Button(toolbar, "Choose sheets…", self.choose_files,
               variant="primary").pack(side="left")
        Button(toolbar, "Choose a folder…", self.choose_folder).pack(
            side="left", padx=(10, 0))
        Button(toolbar, "Settings", self.open_settings).pack(side="right")
        self.skip_var = tk.BooleanVar(value=self.settings.skip_existing)
        ttk.Checkbutton(toolbar, text="Skip sheets already split",
                        variable=self.skip_var,
                        command=self._skip_changed).pack(side="right", padx=(0, 14))

        body = ttk.Panedwindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, pady=(14, 0))
        body.add(self._build_list(body), weight=2)
        body.add(self._build_preview(body), weight=3)

        footer = ttk.Frame(self)
        footer.pack(fill="x", pady=(14, 0))
        self.status = ttk.Label(footer, text="Choose a sheet to begin.",
                                style="Muted.TLabel")
        self.status.pack(side="left")
        self.open_btn = Button(footer, "Open folder", self.open_folder)
        self.open_btn.pack(side="right")
        self.open_btn.enable(False)
        self.cancel_btn = Button(footer, "Stop", self.cancel)
        self.cancel_btn.pack(side="right", padx=(0, 10))
        self.cancel_btn.enable(False)

        self.progress = ttk.Progressbar(self, mode="determinate", maximum=1000,
                                        style="Brand.Horizontal.TProgressbar")
        self.progress.pack(fill="x", pady=(12, 0))

    def _build_header(self):
        header = ttk.Frame(self)
        header.pack(fill="x")
        try:
            img = Image.open(resource("logo-wordmark.png"))
            img.thumbnail((260, 44), Image.LANCZOS)
            self._logo = ImageTk.PhotoImage(img)
            tk.Label(header, image=self._logo, bg=theme.WINDOW).pack(side="left")
        except Exception:
            ttk.Label(header, text="Big Tee Shirt Co.",
                      style="Title.TLabel").pack(side="left")
        ttk.Label(header, text="Sheet Splitter", style="Title.TLabel").pack(
            side="left", padx=(14, 0))
        ttk.Label(header, text="cut-and-sew sheets → one file per piece",
                  style="Muted.TLabel").pack(side="left", padx=(12, 0), pady=(6, 0))
        tk.Frame(self, height=1, bg=theme.BORDER_SOFT).pack(fill="x", pady=(12, 0))

    def _build_list(self, parent):
        wrap = ttk.Frame(parent)
        self.tree = ttk.Treeview(wrap, columns=("pieces", "time"),
                                 selectmode="browse", height=14)
        self.tree.heading("#0", text="Sheet")
        self.tree.heading("pieces", text="Pieces")
        self.tree.heading("time", text="Time")
        self.tree.column("#0", width=280, anchor="w")
        self.tree.column("pieces", width=70, anchor="center")
        self.tree.column("time", width=70, anchor="e")
        self.tree.tag_configure("warn", foreground=theme.AMBER)
        self.tree.tag_configure("fail", foreground=theme.RED)
        self.tree.tag_configure("skip", foreground=theme.MUTED)
        self.tree.pack(side="left", fill="both", expand=True)
        bar = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        bar.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=bar.set)
        self.tree.bind("<<TreeviewSelect>>", self._row_selected)
        self.tree.bind("<Double-1>", lambda _e: self.open_folder())
        return wrap

    def _build_preview(self, parent):
        wrap = ttk.Frame(parent, padding=(14, 0, 0, 0))
        self.canvas = tk.Canvas(wrap, bg=theme.PANEL, highlightthickness=1,
                                highlightbackground=theme.BORDER_SOFT)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self._canvas_resized)
        self.detail = ttk.Label(wrap, text="", wraplength=560, justify="left",
                                style="Warn.TLabel")
        self.detail.pack(fill="x", pady=(10, 0))
        # Finished sizes, so a piece cropped wrong is obvious as a number as
        # well as a picture.
        self.sizes = ttk.Label(wrap, text="", wraplength=560, justify="left",
                               style="Muted.TLabel")
        self.sizes.pack(fill="x", pady=(6, 0))
        return wrap

    # ---------------------------------------------------------------- actions

    def _skip_changed(self):
        self.settings.skip_existing = self.skip_var.get()
        self.settings.save()

    def choose_files(self):
        paths = filedialog.askopenfilenames(
            title="Choose sheets to split",
            filetypes=[("Sheet TIFFs", "*.tif *.tiff"), ("All files", "*.*")])
        if paths:
            self.start(core.gather_sheets(paths))

    def choose_folder(self):
        folder = filedialog.askdirectory(title="Choose a folder of sheets")
        if folder:
            sheets = core.gather_sheets([folder])
            if not sheets:
                messagebox.showinfo(APP_NAME, "No TIFF sheets in that folder.")
                return
            self.start(sheets)

    def open_settings(self):
        SettingsDialog(self.master, self.settings, self._settings_saved)

    def _settings_saved(self):
        core.setup_logging(Path(self.settings.output_root))
        self.skip_var.set(self.settings.skip_existing)
        self._set_status("Settings saved.")

    def cancel(self):
        self.cancel_flag.set()
        self._set_status("Stopping after this piece…")

    def open_folder(self):
        r = self._selected()
        if r and r.folder:
            open_in_explorer(r.folder)
        elif self.results:
            open_in_explorer(self.settings.output_root)

    # ---------------------------------------------------------------- running

    def start(self, sheets: list):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo(APP_NAME, "Still working on the last batch.")
            return
        if not sheets:
            return
        self.results = []
        self.tree.delete(*self.tree.get_children())
        self.canvas.delete("all")
        self.detail.config(text="")
        self.sizes.config(text="")
        self.open_btn.enable(False)
        self.cancel_btn.enable(True)
        self.cancel_flag.clear()
        for sheet in sheets:
            self.tree.insert("", "end", iid=str(sheet), text=f"    {sheet.name}",
                             values=("", ""))
        self.worker = threading.Thread(target=self._run, args=(sheets,), daemon=True)
        self.worker.start()

    def _run(self, sheets: list):
        """Worker thread. One sheet at a time on purpose -- each is gigabytes
        once decompressed, so running several at once would only thrash."""
        results = []
        try:
            for n, sheet in enumerate(sheets, 1):
                if self.cancel_flag.is_set():
                    break
                self.events.put(("row", str(sheet), "working"))

                def step(text, frac, n=n, sheet=sheet):
                    overall = (n - 1 + frac) / len(sheets)
                    self.events.put(
                        ("progress",
                         f"Sheet {n} of {len(sheets)} — {sheet.name}: {text}",
                         overall))

                try:
                    r = core.split_sheet(sheet, self.settings, step,
                                         self.cancel_flag.is_set)
                except core.Cancelled:
                    break
                except Exception:
                    r = core.SheetResult(
                        sheet=str(sheet),
                        message=traceback.format_exc(limit=1).strip())
                results.append(r)
                self.events.put(("result", r, None))
            core.flag_outliers(results)
            self.events.put(("finished", results, None))
        except Exception as exc:  # must not leave a dead-looking window
            self.events.put(("crash", str(exc), None))

    def _pump(self):
        """Drain worker events on the UI thread."""
        try:
            while True:
                kind, a, b = self.events.get_nowait()
                if kind == "progress":
                    self._set_status(a)
                    self.progress["value"] = b * 1000
                elif kind == "row":
                    self.tree.item(a, text="⋯   " + Path(a).name)
                elif kind == "result":
                    self._show_result(a)
                elif kind == "finished":
                    self._finish(a)
                elif kind == "crash":
                    self.cancel_btn.enable(False)
                    messagebox.showerror(APP_NAME, f"Something went wrong:\n\n{a}")
        except queue.Empty:
            pass
        self.after(60, self._pump)

    def _show_result(self, r: core.SheetResult):
        self.results.append(r)
        iid = str(Path(r.sheet))
        if r.skipped:
            mark, tag = "=", "skip"
        elif r.ok:
            mark, tag = "✓", ""
        else:
            mark, tag = "✕", "fail"
        self.tree.item(iid,
                       text=f"{mark}   {Path(r.sheet).name}",
                       values=(r.count if r.ok else "—",
                               "—" if r.skipped else f"{r.seconds:.0f}s"),
                       tags=(tag,) if tag else ())
        if not self.tree.selection():
            self.tree.selection_set(iid)

    def _finish(self, results: list):
        self.cancel_btn.enable(False)
        self.progress["value"] = 1000 if results else 0
        for r in results:  # outlier flags only exist once the batch is done
            if r.warnings:
                self.tree.item(str(Path(r.sheet)), tags=("warn",),
                               text=f"⚠   {Path(r.sheet).name}")
        ok = [r for r in results if r.ok]
        failed = [r for r in results if not r.ok]
        flagged = [r for r in results if r.warnings]
        parts = [f"{len(ok)} of {len(results)} sheets",
                 f"{sum(r.count for r in ok)} pieces"]
        if failed:
            parts.append(f"{len(failed)} failed")
        if flagged:
            parts.append(f"{len(flagged)} to check")
        if self.cancel_flag.is_set():
            parts.append("stopped")
        self._set_status(" · ".join(parts) + ".   Check the preview before printing.")
        self.open_btn.enable(bool(ok))
        self._row_selected()

    # ---------------------------------------------------------------- preview

    def _selected(self):
        sel = self.tree.selection()
        if not sel:
            return None
        return next((r for r in self.results if str(Path(r.sheet)) == sel[0]), None)

    def _row_selected(self, _event=None):
        r = self._selected()
        if not r:
            return
        notes = list(r.warnings)
        if not r.ok:
            notes.append(r.message or "failed")
        elif r.skipped:
            notes.append("Already split — showing the previous result. "
                         "Untick “skip sheets already split” to do it again.")
        self.detail.config(text="\n".join(notes),
                           style="Error.TLabel" if not r.ok else "Warn.TLabel")
        self.sizes.config(text=self._sizes_text(r))
        self.open_btn.enable(bool(r.folder))
        self._draw_preview(r.preview)

    @staticmethod
    def _sizes_text(r: core.SheetResult) -> str:
        if not r.pieces:
            return ""
        parts = [f"{p.index} · {p.width_in:g}×{p.height_in:g}" for p in r.pieces]
        return "Finished sizes in inches   " + "     ".join(parts)

    def _canvas_resized(self, event):
        for label in (self.detail, self.sizes):
            label.config(wraplength=max(200, event.width - 8))
        if self._resize_job:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(
            120, lambda: self._draw_preview(self._preview_path))

    def _draw_preview(self, path: str):
        self._preview_path = path or ""
        self.canvas.delete("all")
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        if not path or not Path(path).exists() or w < 20 or h < 20:
            return
        try:
            img = Image.open(path)
            img.thumbnail((w - 20, h - 20), Image.LANCZOS)
            self._preview_img = ImageTk.PhotoImage(img)
            self.canvas.create_image(w // 2, h // 2, image=self._preview_img)
        except Exception as exc:
            self.canvas.create_text(w // 2, h // 2, fill=theme.MUTED,
                                    text=f"preview unavailable\n{exc}")

    def _set_status(self, text: str):
        self.status.config(text=text)


def main(argv=None) -> int:
    _enable_dpi_awareness()
    argv = list(sys.argv[1:] if argv is None else argv)
    root = tk.Tk()
    root.geometry("1180x780")
    root.minsize(940, 600)
    App(root, argv)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
