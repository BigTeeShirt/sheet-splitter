"""Big Tee brand surface for a Tk window.

The rules come from CaludeJOb/docs/design-guidelines.md, which is the one home
for them. The two that matter most here:

  * The pink is #ec4899 and is not open to being re-picked.
  * ⚠ Never white ink on that pink -- near-black measures 5.35:1, white 3.53:1,
    against AA's 4.5. Every pink surface in this app carries near-black text.

Pink also means "the one main action". Choosing sheets is that action, so it is
the only pink control on the window.
"""

from __future__ import annotations

import sys
import tkinter as tk
from tkinter import ttk

PINK = "#ec4899"
PINK_DARK = "#c63c81"
NEAR_BLACK = "#0a0a0a"

WINDOW = "#171717"        # neutral-900
PANEL = "#1f1f1f"
SUNKEN = "#111111"
BORDER = "#404040"        # neutral-700
BORDER_SOFT = "#262626"   # neutral-800

SELECTED = "#33333a"      # a lifted row, not a pink slab
INK = "#f5f5f5"           # neutral-100
MUTED = "#a3a3a3"         # neutral-400
RED = "#f87171"           # red-400, never red-600 on a dark surface
AMBER = "#fbbf24"

UI_FONT = "Segoe UI" if sys.platform == "win32" else "Helvetica Neue"


def font(size=10, weight="normal"):
    return (UI_FONT, size, weight)


def apply(root: tk.Misc) -> ttk.Style:
    """clam on every platform: the native Windows theme silently ignores most
    colour options, which would leave half the window light grey."""
    style = ttk.Style(root)
    style.theme_use("clam")

    style.configure(".", background=WINDOW, foreground=INK, font=font())
    style.configure("TFrame", background=WINDOW)
    style.configure("Panel.TFrame", background=PANEL)
    style.configure("TLabel", background=WINDOW, foreground=INK)
    style.configure("Muted.TLabel", background=WINDOW, foreground=MUTED)
    style.configure("Title.TLabel", background=WINDOW, foreground=INK,
                    font=font(15, "bold"))
    style.configure("Warn.TLabel", background=WINDOW, foreground=AMBER)
    style.configure("Error.TLabel", background=WINDOW, foreground=RED)

    # clam names these indicatorbackground/indicatorforeground; setting
    # `indicatorcolor` (the name other themes use) silently does nothing and
    # leaves a light box with a smudge in it that reads as a cross, not a tick.
    style.configure("TCheckbutton", background=WINDOW, foreground=MUTED,
                    focuscolor=WINDOW, indicatorbackground=SUNKEN,
                    indicatorforeground=NEAR_BLACK, bordercolor=BORDER,
                    lightcolor=BORDER, darkcolor=BORDER, indicatormargin=(0, 0, 8, 0))
    style.map("TCheckbutton",
              background=[("active", WINDOW)],
              foreground=[("active", INK)],
              indicatorbackground=[("selected", PINK), ("!selected", SUNKEN)],
              indicatorforeground=[("selected", NEAR_BLACK)])  # ⚠ never white on pink

    style.configure("TEntry", fieldbackground=SUNKEN, foreground=INK,
                    bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                    insertcolor=INK, padding=6)
    style.map("TEntry", bordercolor=[("focus", PINK)])

    style.configure("Treeview", background=PANEL, fieldbackground=PANEL,
                    foreground=INK, bordercolor=BORDER_SOFT, rowheight=30,
                    font=font(), lightcolor=BORDER_SOFT, darkcolor=BORDER_SOFT)
    style.configure("Treeview.Heading", background=WINDOW, foreground=MUTED,
                    relief="flat", font=font(9, "bold"), padding=(8, 6))
    style.map("Treeview.Heading", background=[("active", WINDOW)])
    # A selected row is NOT pink. Pink is the one main action, and a full-width
    # pink slab beside a pink button means neither of them is the main thing.
    style.map("Treeview",
              background=[("selected", SELECTED)],
              foreground=[("selected", INK)])

    style.configure("TPanedwindow", background=WINDOW)
    style.configure("Sash", sashthickness=8, gripcount=0)

    style.configure("Brand.Horizontal.TProgressbar", troughcolor=SUNKEN,
                    bordercolor=SUNKEN, background=PINK, lightcolor=PINK,
                    darkcolor=PINK, thickness=6)

    # clam draws a scrollbar's frame from lightcolor/darkcolor as well as
    # bordercolor; leaving those unset puts a white outline and pale arrows on
    # an otherwise dark window. Vertical.* explicitly -- the base name alone
    # does not reach it.
    for name in ("TScrollbar", "Vertical.TScrollbar", "Horizontal.TScrollbar"):
        style.configure(name, background=BORDER, troughcolor=SUNKEN,
                        bordercolor=SUNKEN, lightcolor=SUNKEN, darkcolor=SUNKEN,
                        arrowcolor=MUTED, relief="flat", borderwidth=0)
        style.map(name, background=[("active", MUTED)],
                  arrowcolor=[("active", INK)])
    return style


def round_points(x0, y0, x1, y1, r):
    """Corner points for a rounded rectangle, to be drawn as a smoothed
    polygon -- Tk has no rounded rectangle of its own."""
    return [
        x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
        x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0,
    ]


class Check(tk.Canvas):
    """A tick that reads as a tick.

    clam draws its own checkbutton glyph as a cross at this size, so a ticked
    box said "off" when it meant "on". Drawing it is less work than arguing
    with the theme.
    """

    BOX = 16

    def __init__(self, parent, text, variable=None, command=None, bg=WINDOW):
        self.var = variable if variable is not None else tk.BooleanVar(value=False)
        self.command, self.text = command, text
        self._font = font(10)
        self._hover = False

        probe = tk.Label(parent, text=text, font=self._font)
        tw, th = probe.winfo_reqwidth(), probe.winfo_reqheight()
        probe.destroy()

        super().__init__(parent, width=self.BOX + 10 + tw,
                         height=max(self.BOX, th) + 6, highlightthickness=0,
                         bg=bg, cursor="hand2")
        self.bind("<Button-1>", self._toggle)
        self.bind("<Enter>", self._on_hover)
        self.bind("<Leave>", self._on_hover)
        self._draw()

    def _draw(self):
        self.delete("all")
        h = int(self["height"])
        top = (h - self.BOX) // 2
        on = bool(self.var.get())
        fill = PINK if on else SUNKEN
        self.create_polygon(round_points(1, top, self.BOX, top + self.BOX - 1, 4),
                            smooth=True, splinesteps=12, fill=fill,
                            outline=PINK if on else BORDER, width=1)
        if on:
            # ⚠ near-black on the pink, like everything else that sits on it
            self.create_line(5, top + 8, 8, top + 11, 13, top + 4,
                             fill=NEAR_BLACK, width=2, capstyle="round",
                             joinstyle="round")
        self.create_text(self.BOX + 10, h // 2, anchor="w", text=self.text,
                         font=self._font, fill=INK if self._hover else MUTED)

    def _on_hover(self, event):
        self._hover = event.type == tk.EventType.Enter
        self._draw()

    def _toggle(self, _event):
        self.var.set(not self.var.get())
        self._draw()
        if self.command:
            self.command()


class Button(tk.Canvas):
    """A rounded slab, because ttk cannot round a corner.

    Variants are roles, not colours -- `primary` is THE one main action on the
    window and nothing else is pink; everything else is `secondary`, the
    bordered base button.
    """

    def __init__(self, parent, text, command=None, variant="secondary",
                 pad=(16, 9), radius=6, bg=WINDOW):
        self.variant, self.radius, self.canvas_bg = variant, radius, bg
        self._font = font(10, "bold" if variant == "primary" else "normal")
        self.text, self.command = text, command
        self._enabled, self._hover, self._pressed = True, False, False

        probe = tk.Label(parent, text=text, font=self._font)
        w = probe.winfo_reqwidth() + pad[0] * 2
        h = probe.winfo_reqheight() + pad[1] * 2
        probe.destroy()

        super().__init__(parent, width=w, height=h, highlightthickness=0,
                         bg=bg, cursor="hand2" if command else "arrow")
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self._draw()

    # -- painting

    def _colours(self):
        if not self._enabled:
            return PANEL, BORDER_SOFT, MUTED
        if self.variant == "primary":
            fill = PINK_DARK if self._pressed else PINK
            # ⚠ near-black on pink, measured. Not white, ever.
            return fill, fill, NEAR_BLACK
        fill = PANEL if (self._hover or self._pressed) else self.canvas_bg
        return fill, BORDER, INK

    def _draw(self):
        self.delete("all")
        w, h = int(self["width"]), int(self["height"])
        fill, outline, ink = self._colours()
        self.create_polygon(round_points(1, 1, w - 1, h - 1, self.radius),
                            smooth=True, splinesteps=24, fill=fill,
                            outline=outline, width=1)
        self.create_text(w // 2, h // 2, text=self.text, fill=ink,
                         font=self._font)

    # -- state

    def enable(self, on: bool = True):
        self._enabled = on
        self.configure(cursor="hand2" if on and self.command else "arrow")
        self._draw()

    def set_text(self, text: str):
        self.text = text
        self._draw()

    def _on_enter(self, _e):
        self._hover = True
        self._draw()

    def _on_leave(self, _e):
        self._hover = self._pressed = False
        self._draw()

    def _on_press(self, _e):
        if self._enabled:
            self._pressed = True
            self._draw()

    def _on_release(self, _e):
        was = self._pressed
        self._pressed = False
        self._draw()
        if was and self._enabled and self.command:
            self.command()
