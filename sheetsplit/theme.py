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

import math
import sys
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageDraw, ImageTk

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

# The apps run at a bumped root size, so their text-sm is nearer 16px than 14.
# Matching that reads as the same family of screen -- and is a good deal easier
# on the eyes across a workshop desk.
BASE = 12
SCALE = 1.0


def set_scale(percent: float) -> None:
    """Text size, set once before the window is built."""
    global SCALE
    SCALE = max(0.8, min(2.0, percent / 100.0))


def font(size=None, weight="normal"):
    return (UI_FONT, max(8, round((BASE if size is None else size) * SCALE)), weight)


def px(n: int) -> int:
    """Scale a pixel measurement alongside the type, so a bigger text size
    doesn't leave everything rattling around in the same small boxes."""
    return max(1, round(n * SCALE))


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
                    font=font(18, "bold"))
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
                    insertcolor=INK, padding=px(8))
    style.map("TEntry", bordercolor=[("focus", PINK)])

    style.configure("Treeview", background=PANEL, fieldbackground=PANEL,
                    foreground=INK, bordercolor=BORDER_SOFT, rowheight=px(36),
                    font=font(), lightcolor=BORDER_SOFT, darkcolor=BORDER_SOFT)
    style.configure("Treeview.Heading", background=WINDOW, foreground=MUTED,
                    relief="flat", font=font(11, "bold"), padding=(10, 8))
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
                    darkcolor=PINK, thickness=px(8))

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


def slab(width, height, radius, fill, outline, bg, supersample=4):
    """A rounded rectangle as an image.

    ⚠ Drawn rather than made from canvas primitives on purpose. Tk's canvas has
    no antialiasing, so a polygon traced round a 7px radius rasterises into a
    single 45-degree cut -- the corners came out chamfered, not rounded. Pillow
    draws it at 4x and downsamples, which is the only way to get a clean curve.
    """
    ss = supersample
    big = Image.new("RGB", (width * ss, height * ss), bg)
    draw = ImageDraw.Draw(big)
    draw.rounded_rectangle(
        [ss // 2, ss // 2, width * ss - ss // 2 - 1, height * ss - ss // 2 - 1],
        radius=radius * ss, fill=fill, outline=outline, width=ss)
    return ImageTk.PhotoImage(big.resize((width, height), Image.LANCZOS))


class Check(tk.Canvas):
    """A tick that reads as a tick.

    clam draws its own checkbutton glyph as a cross at this size, so a ticked
    box said "off" when it meant "on". Drawing it is less work than arguing
    with the theme.
    """

    def __init__(self, parent, text, variable=None, command=None, bg=WINDOW):
        self.BOX = px(18)
        self.var = variable if variable is not None else tk.BooleanVar(value=False)
        self.command, self.text = command, text
        self._font = font()
        self._hover = False
        self._faces, self._bg = {}, bg

        probe = tk.Label(parent, text=text, font=self._font)
        tw, th = probe.winfo_reqwidth(), probe.winfo_reqheight()
        probe.destroy()

        super().__init__(parent, width=self.BOX + px(10) + tw,
                         height=max(self.BOX, th) + px(8), highlightthickness=0,
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
        box = self.BOX
        key = (fill, on)
        if key not in self._faces:
            self._faces[key] = slab(box, box, px(4), fill,
                                    PINK if on else BORDER, self._bg)
        self.create_image(1, top, anchor="nw", image=self._faces[key])
        if on:
            # ⚠ near-black on the pink, like everything else that sits on it
            self.create_line(box * 0.27, top + box * 0.52,
                             box * 0.45, top + box * 0.70,
                             box * 0.76, top + box * 0.28,
                             fill=NEAR_BLACK, width=px(2), capstyle="round",
                             joinstyle="round")
        self.create_text(self.BOX + px(10), h // 2, anchor="w", text=self.text,
                         font=self._font, fill=INK if self._hover else MUTED)

    def refresh(self):
        """Redraw after the variable was set from code rather than clicked."""
        self._draw()

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

    Anthony asked for every button pink, so `primary` is the default and the
    bordered `secondary` is left in for anything that should step back later.

    ⚠ The near-black text on the pink is not a style choice and stays whatever
    the buttons do: measured 5.35:1 against white's 3.53:1, on a 4.5 threshold.
    """

    def __init__(self, parent, text, command=None, variant="primary",
                 pad=(18, 11), radius=7, bg=WINDOW):
        self.variant, self.canvas_bg = variant, bg
        self.radius = px(radius)
        self._faces = {}
        # Both variants are semibold in button.tsx; Tk has no semibold, so bold.
        self._font = font(weight="bold")
        self.text, self.command = text, command
        self._enabled, self._hover, self._pressed = True, False, False

        probe = tk.Label(parent, text=text, font=self._font)
        w = probe.winfo_reqwidth() + px(pad[0]) * 2
        h = probe.winfo_reqheight() + px(pad[1]) * 2
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
        key = (fill, outline)
        if key not in self._faces:
            self._faces[key] = slab(w, h, self.radius, fill, outline,
                                    self.canvas_bg)
        self.create_image(0, 0, anchor="nw", image=self._faces[key])
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
