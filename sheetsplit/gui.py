"""Sheet Splitter -- the window the print PC runs.

Pick sheets (or a folder), watch it work, then check the numbered preview before
sending anything to Edge Print. The check is the point: a wrong split is far
cheaper to spot here than on printed media.
"""

from __future__ import annotations

import datetime
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

try:  # Tk has no OS drag-and-drop of its own; this wraps the tkdnd library
    from tkinterdnd2 import DND_FILES, TkinterDnD
    DND = True
except Exception:  # without it everything still works, just via the buttons
    DND_FILES, TkinterDnD, DND = None, None, False

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


def _apply_window_chrome(root) -> None:
    """Ask Windows for the rounded corners and the dark title bar.

    Windows 11 rounds windows itself, but only for apps that opt in -- a Tk
    window otherwise gets square corners and a light title strip sitting above a
    dark app. Both are single DWM attributes, and both are no-ops on Windows 10,
    which is why neither is worth branching on a version check.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        DARK_TITLE_BAR, CORNER_PREFERENCE, ROUND = 20, 33, 2
        for attribute, value in ((DARK_TITLE_BAR, 1), (CORNER_PREFERENCE, ROUND)):
            v = ctypes.c_int(value)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attribute, ctypes.byref(v), ctypes.sizeof(v))
    except Exception:
        pass  # older Windows simply doesn't have these; square corners are fine


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
        ("margin_in", "Margin outside the line (in)", "keeps the whole cut line"),
        ("min_piece_in", "Smallest piece (in)", "anything shorter is ignored"),
        ("ink_threshold", "Ink threshold (0–255)",
         "raise it if blank media is reading as ink"),
        ("text_scale", "Text size (%)",
         "100 is normal, 130 is noticeably bigger; applies next time you open it"),
        ("diagnostics_dir", "Send diagnostics to",
         "a Synology folder puts them where they can be looked at; blank = Desktop"),
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
            if key == "diagnostics_dir":
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
        chosen = filedialog.askdirectory(title="Where should diagnostics go?")
        if chosen:
            self.vars["diagnostics_dir"].set(chosen)

    def _save(self):
        try:
            s = self.settings
            s.margin_in = max(0.0, float(self.vars["margin_in"].get()))
            s.min_piece_in = max(0.05, float(self.vars["min_piece_in"].get()))
            s.ink_threshold = min(254, max(0, int(float(self.vars["ink_threshold"].get()))))
            s.text_scale = min(200, max(80, int(float(self.vars["text_scale"].get()))))
            s.diagnostics_dir = self.vars["diagnostics_dir"].get().strip()
        except ValueError:
            messagebox.showerror(APP_NAME, "Those need to be numbers.", parent=self)
            return
        s.save()
        self.on_save()
        self.destroy()


class App(ttk.Frame):
    def __init__(self, master, argv_paths, autostart=False, dest=None):
        super().__init__(master, padding=(18, 14, 18, 16))
        self.master = master
        self.settings = core.Settings.load()
        if dest:
            self.settings.dest_dir = dest
        core.setup_logging()
        # Worth recording: if the native library fails to load, drag-and-drop
        # quietly stops existing and the buttons carry on as if nothing is wrong.
        core.log.info("drag and drop: %s", "available" if DND else "UNAVAILABLE")

        self.results: list = []
        self.queue: list = []          # sheets waiting for Start
        self.queue_keys: set = set()
        self.scan_queue: list = []     # sheets waiting to be looked at
        self.scan_thread = None
        self.previews: dict = {}       # sheet -> (preview path, piece count)
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
        self.master.protocol("WM_DELETE_WINDOW", self._on_close)
        core.sweep_old_work_dirs()

        if argv_paths:  # sheets dragged onto the exe fill the list
            self.add_sheets(core.gather_sheets(argv_paths))
            if autostart:
                self.after(200, self.run)

    # ---------------------------------------------------------------- layout

    def _build(self):
        self.master.title(WINDOW_TITLE)
        self.master.configure(bg=theme.WINDOW)
        theme.apply(self.master)
        try:
            self.master.iconphoto(True, tk.PhotoImage(file=resource("icon.png")))
        except Exception:
            pass

        self._build_menu()
        self._build_header()

        # Load the list up first, then press Start -- nothing runs on its own.
        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x", pady=(16, 0))
        Button(toolbar, "Add sheets…", self.choose_files).pack(side="left")
        Button(toolbar, "Add a folder…", self.choose_folder).pack(
            side="left", padx=(10, 0))
        self.clear_btn = Button(toolbar, "Clear list", self.clear_list)
        self.clear_btn.pack(side="left", padx=(10, 0))
        self.clear_btn.enable(False)

        tk.Frame(self, height=1, bg=theme.BORDER_SOFT).pack(fill="x", pady=(14, 0))

        body = ttk.Panedwindow(self, orient="horizontal")
        footer = ttk.Frame(self)
        self.status = ttk.Label(footer, text="Add some sheets, then press Start.",
                                style="Muted.TLabel")
        self.status.pack(side="left")
        self.open_btn = Button(footer, "Open folder", self.open_folder)
        self.open_btn.pack(side="right")
        self.open_btn.enable(False)
        self.cancel_btn = Button(footer, "Stop", self.cancel)
        self.cancel_btn.pack(side="right", padx=(0, 10))
        self.cancel_btn.enable(False)
        self.start_btn = Button(footer, "Start", self.run)
        self.start_btn.pack(side="right", padx=(0, 10))
        self.start_btn.enable(False)

        self.progress = ttk.Progressbar(self, mode="determinate", maximum=1000,
                                        style="Brand.Horizontal.TProgressbar")
        # Bottom-up, and the body last: the packer drops whatever will not fit,
        # and the controls must never be the thing that goes.
        self.progress.pack(side="bottom", fill="x", pady=(12, 0))
        footer.pack(side="bottom", fill="x", pady=(14, 0))
        body.pack(fill="both", expand=True, pady=(14, 0))
        body.add(self._build_list(body), weight=2)
        body.add(self._build_preview(body), weight=3)
        self._refresh_dest()
        # Place the sash deliberately: left to itself the list can take almost
        # the whole width and leave the preview a sliver.
        self.after(200, lambda: self._place_sash(body))

    def _place_sash(self, body):
        try:
            width = body.winfo_width()
            if width > 400:
                body.sashpos(0, int(width * 0.45))
        except Exception:
            pass

    def _build_menu(self):
        """One dropdown, for the two things that are not part of the job itself.
        Everything that does work stays on the window as a button."""
        menubar = tk.Menu(self.master)
        settings = tk.Menu(menubar, tearoff=0)
        settings.add_command(label="Settings…", accelerator="Cmd+,",
                             command=self.open_settings)
        settings.add_command(label="Save diagnostics…",
                             command=self.save_diagnostics)
        menubar.add_cascade(label="Settings", menu=settings)
        self.master.config(menu=menubar)
        self.master.bind_all("<Command-comma>", lambda _e: self.open_settings())
        try:  # macOS puts Preferences in the application menu as well
            self.master.createcommand("tk::mac::ShowPreferences",
                                      self.open_settings)
        except Exception:
            pass

    def _build_header(self):
        header = ttk.Frame(self)
        header.pack(fill="x")
        try:
            img = Image.open(resource("logo-wordmark.png"))
            img.thumbnail((780, 132), Image.LANCZOS)
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

        # Bottom strip first, then the list fills what is left: sheets go in
        # above, and this says where they come out.
        under = ttk.Frame(wrap)
        under.pack(side="bottom", fill="x", pady=(12, 0))
        self.choose_dest_btn = Button(under, "Choose destination…",
                                      self.choose_dest, pad=(14, 8))
        self.choose_dest_btn.pack(side="left")
        self.dest_label = ttk.Label(under, text="", style="Muted.TLabel")
        self.dest_label.pack(side="left", padx=(12, 0))

        listing = ttk.Frame(wrap)
        listing.pack(side="top", fill="both", expand=True)
        self.tree = ttk.Treeview(listing, columns=("pieces", "time"),
                                 selectmode="browse", height=8)
        self.tree.heading("#0", text="Sheet")
        self.tree.heading("pieces", text="Pieces")
        self.tree.heading("time", text="Time")
        self.tree.column("#0", width=280, anchor="w")
        self.tree.column("pieces", width=70, anchor="center")
        self.tree.column("time", width=90, anchor="e")
        self.tree.tag_configure("warn", foreground=theme.AMBER)
        self.tree.tag_configure("fail", foreground=theme.RED)
        self.tree.tag_configure("skip", foreground=theme.MUTED)
        self.tree.pack(side="left", fill="both", expand=True)
        bar = ttk.Scrollbar(listing, orient="vertical", command=self.tree.yview)
        bar.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=bar.set)
        self.tree.bind("<<TreeviewSelect>>", self._row_selected)
        self.tree.bind("<Double-1>", lambda _e: self.open_folder())

        self.empty_hint = ttk.Label(
            listing, style="Muted.TLabel", justify="center", anchor="center",
            text=("Drag sheets here\nor press Add sheets…" if DND
                  else "Press Add sheets… to begin"))
        self.empty_hint.place(relx=0.5, rely=0.45, anchor="center")

        if DND:
            for widget in (self.tree, self.empty_hint):
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", self._on_drop)
        return wrap

    def _build_preview(self, parent):
        """⚠ The preview is a Label, not a canvas image.

        On a canvas it drew without error and without appearing -- the log said
        "preview drawn" while the panel stayed empty on the Mac Mini, and it
        rendered fine on the build runner, so it was never reproducible here. A
        Label showing a PhotoImage is the plainest thing Tk offers and takes
        that whole class of problem off the table.
        """
        wrap = ttk.Frame(parent, padding=(14, 0, 0, 0))
        self.preview_box = tk.Frame(wrap, bg=theme.PANEL, highlightthickness=1,
                                    highlightbackground=theme.BORDER_SOFT,
                                    width=460, height=380)
        self.preview_box.pack(fill="both", expand=True)
        self.preview_box.pack_propagate(False)
        self.preview_label = tk.Label(self.preview_box, bg=theme.PANEL,
                                      fg=theme.MUTED, font=theme.font(),
                                      text="the preview appears here")
        self.preview_label.pack(fill="both", expand=True)
        self.preview_box.bind("<Configure>", self._canvas_resized)

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

    def choose_files(self):
        paths = filedialog.askopenfilenames(
            title="Choose sheets to split",
            filetypes=[("Sheet TIFFs", "*.tif *.tiff"), ("All files", "*.*")])
        if paths:
            self.add_sheets(core.gather_sheets(paths))

    def choose_folder(self):
        folder = filedialog.askdirectory(title="Choose a folder of sheets")
        if folder:
            sheets = core.gather_sheets([folder])
            if not sheets:
                messagebox.showinfo(APP_NAME, "No TIFF sheets in that folder.")
                return
            self.add_sheets(sheets)

    def open_settings(self):
        SettingsDialog(self.master, self.settings, self._settings_saved)

    def _settings_saved(self):
        core.setup_logging()
        self._set_status("Settings saved.")

    def save_diagnostics(self):
        """One zip holding the log, the settings, and the preview and ink mask
        for each sheet in this batch -- never the sheets themselves. Enough to
        work out what went wrong from a machine that can't see this one."""
        stamp = datetime.datetime.now().strftime("%Y-%m-%d-%H%M")
        try:
            out = core.build_diagnostics(self.results, self.settings, stamp)
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Could not save diagnostics:\n\n{exc}")
            return
        self._set_status(f"Diagnostics saved: {out}")
        open_in_explorer(str(out.parent))

    def cancel(self):
        self.cancel_flag.set()
        self._set_status("Stopping after this piece…")

    def open_folder(self):
        r = self._selected()
        if r and r.folder:
            open_in_explorer(r.folder)
        elif self.results and self.results[0].folder:
            open_in_explorer(str(Path(self.results[0].folder).parent))

    # ---------------------------------------------------------------- running

    def add_sheets(self, sheets: list):
        """Queue sheets up. Nothing runs until Start is pressed."""
        if self._running():
            messagebox.showinfo(APP_NAME, "Still working on the last batch.")
            return
        for sheet in sheets:
            key = str(sheet)
            if key in self.queue_keys:
                continue
            self.queue.append(sheet)
            self.queue_keys.add(key)
            self.tree.insert("", "end", iid=key, text=f"    {sheet.name}",
                             values=("", "reading…"))
            self.scan_queue.append(sheet)
        self._start_scanner()
        self._refresh_actions()
        self._set_status(f"{len(self.queue)} sheet(s) ready — press Start."
                         if self.queue else "Add some sheets, then press Start.")

    def _start_scanner(self):
        """Look at newly added sheets in the background so their previews are
        ready before anyone presses Start."""
        if self.scan_thread and self.scan_thread.is_alive():
            return
        self.scan_thread = threading.Thread(target=self._scan_loop, daemon=True)
        self.scan_thread.start()

    def _scan_loop(self):
        while self.scan_queue:
            if self.cancel_flag.is_set():
                return
            sheet = self.scan_queue.pop(0)
            try:
                preview, count = core.scan_sheet(sheet, self.settings)
                self.events.put(("scanned", (str(sheet), preview, count), None))
            except Exception as exc:
                core.log.exception("could not scan %s", sheet)
                self.events.put(("scanfail", (str(sheet), str(exc)), None))

    def clear_list(self):
        if self._running():
            return
        self.queue, self.queue_keys, self.results = [], set(), []
        self.scan_queue, self.previews = [], {}
        self.tree.delete(*self.tree.get_children())
        self._clear_preview()
        self.progress["value"] = 0
        self.open_btn.enable(False)
        self._refresh_actions()
        self._set_status("Add some sheets, then press Start.")

    def run(self):
        """Start on the whole list."""
        core.log.info("Start pressed: %d sheet(s) queued, destination %r, "
                      "already running=%s", len(self.queue),
                      self.settings.dest_dir, self._running())
        if self._running():
            return
        # ⚠ Start stays pink and pressable. Greying it out to enforce "choose a
        # destination first" just produced a dead button with no explanation --
        # it reads as broken, which is exactly how it was reported.
        if not self.queue:
            self._set_status("Add some sheets first.")
            self.choose_files()
            return
        if not self.settings.dest_dir:
            self._set_status("Choose where the pieces should go.")
            self.choose_dest()
            if not self.settings.dest_dir:
                return
        self.results = []
        for sheet in self.queue:  # clear any marks from a previous run
            self.tree.item(str(sheet), text=f"    {sheet.name}", values=("", ""),
                           tags=())
        self._clear_preview()
        self.open_btn.enable(False)
        self.cancel_btn.enable(True)
        self.cancel_flag.clear()
        self.worker = threading.Thread(target=self._run, args=(list(self.queue),),
                                       daemon=True)
        self.worker.start()
        self._refresh_actions()

    def _on_close(self):
        """Nothing survives the window closing except the pieces themselves."""
        self.cancel_flag.set()
        self._preview_img = None
        core.discard_work_dir()
        self.master.destroy()

    def _running(self) -> bool:
        return bool(self.worker and self.worker.is_alive())

    def _refresh_actions(self):
        running = self._running()
        self.start_btn.enable(not running)
        self.clear_btn.enable(bool(self.queue) and not running)
        if self.queue:
            self.empty_hint.place_forget()
        else:
            self.empty_hint.place(relx=0.5, rely=0.45, anchor="center")

    def _clear_preview(self):
        # ⚠ Clearing the canvas is not enough: any resize redraws from
        # _preview_path, so the preview reappeared moments after Clear list.
        self._preview_path = ""
        self._preview_img = None
        self.detail.config(text="")
        self.sizes.config(text="")

    # ------------------------------------------------------------ destination

    def choose_dest(self):
        chosen = filedialog.askdirectory(title="Where should the pieces go?")
        if chosen:
            self.settings.dest_dir = chosen
            self.settings.save()
            self._refresh_dest()
            self._refresh_actions()

    def _refresh_dest(self):
        chosen = bool(self.settings.dest_dir)
        self.dest_label.config(
            text=self.settings.dest_dir if chosen
            else "Choose destination",
            style="Muted.TLabel" if chosen else "Warn.TLabel")

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

                def preview_ready(path, count, sheet=sheet):
                    self.events.put(("preview", (str(sheet), path, count), None))

                try:
                    r = core.split_sheet(sheet, self.settings, step,
                                         self.cancel_flag.is_set, preview_ready)
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
        """Drain worker events on the UI thread.

        ⚠ Everything is inside the try, and the reschedule is in the finally.
        This runs on Tk's `after` chain, so an exception escaping it stops the
        chain for good: progress, results and buttons all quietly stop updating
        and the window looks like it has ignored you.
        """
        try:
            while True:
                kind, a, b = self.events.get_nowait()
                if kind == "progress":
                    self._set_status(a)
                    self.progress["value"] = b * 1000
                elif kind == "row":
                    self.tree.item(a, text="⋯   " + Path(a).name)
                    self.tree.selection_set(a)   # so its preview shows as it lands
                elif kind == "preview":
                    self._preview_ready(*a)
                elif kind == "scanned":
                    self._scanned(*a)
                elif kind == "scanfail":
                    sheet, message = a
                    if self.tree.exists(sheet):
                        self.tree.item(sheet, values=("—", "unreadable"),
                                       tags=("fail",))
                        self.detail.config(text=message, style="Error.TLabel")
                elif kind == "result":
                    self._show_result(a)
                elif kind == "finished":
                    self._finish(a)
                elif kind == "crash":
                    self.cancel_btn.enable(False)
                    messagebox.showerror(APP_NAME, f"Something went wrong:\n\n{a}")
        except queue.Empty:
            pass
        except Exception:
            core.log.exception("error handling a UI event")
        finally:
            self.after(60, self._pump)

    def _scanned(self, sheet: str, path: str, count: int):
        """A sheet has been looked at. Its preview is ready before Start."""
        self.previews[sheet] = (path, count)
        if self.tree.exists(sheet):
            self.tree.item(sheet, values=(count, "ready"))
        selection = self.tree.selection()
        if not selection or selection[0] == sheet:
            if not selection:
                self.tree.selection_set(sheet)
            self._draw_preview(path)

    def _preview_ready(self, sheet: str, path: str, count: int):
        """The pieces have been found; they are still being written. Showing it
        now means the count can be checked without waiting for the cutting."""
        if self.tree.exists(sheet):
            self.tree.item(sheet, values=(count, "cutting…"))
        if not self.tree.selection() or self.tree.selection()[0] == sheet:
            self._draw_preview(path)

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
        self._refresh_actions()
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
            selection = self.tree.selection()
            if selection and selection[0] in self.previews:
                self._draw_preview(self.previews[selection[0]][0])
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
        # Non-breaking spaces inside an entry, ordinary ones between them, so a
        # wrap never splits "7 · 5.05×5.15" across two lines.
        parts = [f"{p.index} · {p.width_in:.2f}×{p.height_in:.2f}"
                 for p in r.pieces]
        return "Finished sizes in inches    " + "     ".join(parts)

    def _canvas_resized(self, event):
        for label in (self.detail, self.sizes):
            label.config(wraplength=max(200, event.width - 8))
        if self._resize_job:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(
            120, lambda: self._draw_preview(self._preview_path))

    def _draw_preview(self, path: str):
        self._preview_path = path or ""
        w = self.preview_box.winfo_width() - 20
        h = self.preview_box.winfo_height() - 20
        if w < 40 or h < 40:
            return          # not laid out yet; the Configure binding calls back
        if not path or not Path(path).exists():
            self._preview_img = None
            self.preview_label.config(image="", text="the preview appears here")
            return
        try:
            img = Image.open(path)
            img.thumbnail((w, h), Image.LANCZOS)
            self._preview_img = ImageTk.PhotoImage(img)
            self.preview_label.config(image=self._preview_img, text="")
            core.log.info("preview shown: %s at %dx%d in a %dx%d panel",
                          Path(path).name, img.width, img.height, w, h)
        except Exception as exc:
            core.log.exception("could not show preview %s", path)
            self._preview_img = None
            self.preview_label.config(image="", text=f"preview unavailable\n{exc}")

    def _set_status(self, text: str):
        self.status.config(text=text)


def main(argv=None) -> int:
    _enable_dpi_awareness()
    argv = list(sys.argv[1:] if argv is None else argv)
    # --start splits straight away instead of waiting for the button. Sheets
    # dragged onto the icon only fill the list; this is for automation, and it
    # is what the build uses to prove the packaged app really splits a sheet.
    autostart = "--start" in argv
    dest = None
    if "--dest" in argv:
        i = argv.index("--dest")
        dest = argv[i + 1] if i + 1 < len(argv) else None
        del argv[i:i + 2]
    argv = [a for a in argv if a != "--start"]

    theme.set_scale(core.Settings.load().text_scale)
    root = TkinterDnD.Tk() if DND else tk.Tk()
    # Never open wider than the screen: on a 1024x768 display the preferred size
    # puts Settings and Open folder off the right-hand edge, where nobody can
    # reach them.
    width = max(820, min(1280, root.winfo_screenwidth() - 80))
    height = max(560, min(880, root.winfo_screenheight() - 120))
    root.geometry(f"{width}x{height}")
    root.minsize(min(940, width), min(600, height))
    App(root, argv, autostart=autostart, dest=dest)
    _apply_window_chrome(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
