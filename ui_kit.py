"""
ui_kit.py — Reusable UI components that give ChainEX its web-app look.

Provides
--------
lerp_hex        Linearly interpolate between two #RRGGBB colours.
AnimatedButton  Flat button with smooth 140 ms hover colour transition.
RoundedCard     Canvas-backed container with true rounded corners.
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable, Optional


# ── Colour helpers ────────────────────────────────────────────────────────────

def lerp_hex(c1: str, c2: str, t: float) -> str:
    """Linearly interpolate between two ``#RRGGBB`` colour strings.

    Args:
        c1: Start colour (``"#RRGGBB"``).
        c2: End colour   (``"#RRGGBB"``).
        t:  Mix factor 0.0 → c1, 1.0 → c2.

    Returns:
        Interpolated ``"#RRGGBB"`` string.
    """
    def _ch(s: str, i: int) -> int:
        return int(s[i: i + 2], 16)

    r = int(_ch(c1, 1) + (_ch(c2, 1) - _ch(c1, 1)) * t)
    g = int(_ch(c1, 3) + (_ch(c2, 3) - _ch(c1, 3)) * t)
    b = int(_ch(c1, 5) + (_ch(c2, 5) - _ch(c1, 5)) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


# ── Animation constants ───────────────────────────────────────────────────────

_ANIM_TOTAL_MS: int = 140
_ANIM_FPS:      int = 60
_ANIM_STEP_MS:  int = max(1, 1000 // _ANIM_FPS)          # ≈ 16 ms
_ANIM_STEPS:    int = max(1, _ANIM_TOTAL_MS // _ANIM_STEP_MS)   # ≈ 8 frames


# ── AnimatedButton ────────────────────────────────────────────────────────────

class AnimatedButton(tk.Button):
    """Flat button with a smooth bg/fg colour fade on hover.

    Drop-in replacement for ``HoverButton`` — same constructor signature.

    Args:
        master:   Parent widget.
        hover_bg: Background colour when hovered.
        hover_fg: Foreground (text) colour when hovered.
        **kw:     All other ``tk.Button`` keyword arguments.
    """

    def __init__(
        self,
        master: tk.Widget,
        hover_bg: str = "#22D3EE",
        hover_fg: str = "#000000",
        **kw,
    ) -> None:
        self._hbg: str = hover_bg
        self._hfg: str = hover_fg
        self._anim_job: Optional[int] = None
        self._anim_t:   float         = 0.0   # 0 = resting, 1 = fully hovered

        kw.setdefault("relief",           "flat")
        kw.setdefault("cursor",           "hand2")
        kw.setdefault("borderwidth",      0)
        kw.setdefault("activebackground", hover_bg)
        kw.setdefault("activeforeground", hover_fg)
        super().__init__(master, **kw)

        # Capture resting colours after super().__init__ applies them
        self._rbg: str = str(self["bg"])
        self._rfg: str = str(self["fg"])

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)

    # ── Public API ────────────────────────────────────────────────────────────

    def set_style(self, bg: str, fg: str) -> None:
        """Update the resting bg/fg instantly and keep hover colours correct."""
        self._rbg = bg
        self._rfg = fg
        self.config(bg=bg, fg=fg)

    # ── Animation ─────────────────────────────────────────────────────────────

    def _on_enter(self, _: tk.Event | None = None) -> None:
        if str(self["state"]) == "disabled":
            return
        self._animate(forward=True)

    def _on_leave(self, _: tk.Event | None = None) -> None:
        self._animate(forward=False)

    def _animate(self, forward: bool) -> None:
        if self._anim_job is not None:
            try:
                self.after_cancel(self._anim_job)
            except Exception:
                pass
            self._anim_job = None
        self._tick(forward)

    def _tick(self, forward: bool) -> None:
        step = 1.0 / _ANIM_STEPS
        if forward:
            self._anim_t = min(1.0, self._anim_t + step)
        else:
            self._anim_t = max(0.0, self._anim_t - step)

        try:
            self.config(
                bg=lerp_hex(self._rbg, self._hbg, self._anim_t),
                fg=lerp_hex(self._rfg, self._hfg, self._anim_t),
            )
        except Exception:
            return

        keep_going = (forward and self._anim_t < 1.0) or (
            not forward and self._anim_t > 0.0
        )
        if keep_going:
            self._anim_job = self.after(_ANIM_STEP_MS, lambda: self._tick(forward))
        else:
            self._anim_job = None


# ── RoundedCard ───────────────────────────────────────────────────────────────

class RoundedCard(tk.Canvas):
    """Canvas that draws a rounded-rectangle and exposes an ``inner`` Frame.

    Use ``card.inner`` as the parent for child widgets exactly as you would
    a plain ``tk.Frame``.

    Args:
        parent:   Parent widget.
        radius:   Corner radius in pixels.
        bg_card:  Fill colour of the card.
        border:   Border / outline colour.
        pad:      Extra inset from the canvas edge before the rounded rect.
        **kw:     Forwarded to ``tk.Canvas`` (width, height, etc.).

    Example::

        card = RoundedCard(parent, radius=10, bg_card="#131829",
                           border="#334155", height=120)
        card.pack(fill="x", padx=14, pady=6)
        tk.Label(card.inner, text="Hello", bg=card.bg_card).pack(padx=12)
    """

    def __init__(
        self,
        parent:   tk.Widget,
        radius:   int = 10,
        bg_card:  str = "#131829",
        border:   str = "#334155",
        pad:      int = 1,
        **kw,
    ) -> None:
        # Canvas bg must match the *parent* bg so the corners look transparent
        try:
            parent_bg = str(parent.cget("bg"))
        except Exception:
            parent_bg = "#0A0E1A"

        kw.setdefault("highlightthickness", 0)
        kw.setdefault("bd", 0)
        super().__init__(parent, bg=parent_bg, **kw)

        self.bg_card: str = bg_card
        self._border = border
        self._radius = radius
        self._pad    = pad

        # Inner frame — place children here
        self.inner = tk.Frame(self, bg=bg_card)
        self._inner_id = self.create_window(0, 0, window=self.inner, anchor="nw")

        self.bind("<Configure>", self._redraw)

    # ── Drawing ───────────────────────────────────────────────────────────────

    def _redraw(self, _: tk.Event | None = None) -> None:
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 4 or h < 4:
            return

        p  = self._pad
        r  = self._radius
        x1, y1, x2, y2 = p, p, w - p, h - p

        self.delete("card")

        # 1. Border layer (outer)
        self._rounded_poly(x1, y1, x2, y2, r, fill=self._border)
        # 2. Fill layer (1 px inset so border is visible)
        inner_r = max(1, r - 1)
        self._rounded_poly(x1 + 1, y1 + 1, x2 - 1, y2 - 1, inner_r,
                            fill=self.bg_card)

        # Reposition and resize the inner tk.Frame
        inner_w = max(1, w - 2 * p - 2)
        inner_h = max(1, h - 2 * p - 2)
        self.coords(self._inner_id, p + 1, p + 1)
        self.inner.config(width=inner_w, height=inner_h)

    def _rounded_poly(
        self, x1: int, y1: int, x2: int, y2: int, r: int, fill: str
    ) -> None:
        """Draw a smooth rounded rectangle as a polygon."""
        pts = [
            x1 + r, y1,      x2 - r, y1,
            x2,     y1,      x2,     y1 + r,
            x2,     y2 - r,  x2,     y2,
            x2 - r, y2,      x1 + r, y2,
            x1,     y2,      x1,     y2 - r,
            x1,     y1 + r,  x1,     y1,
        ]
        self.create_polygon(pts, smooth=True, fill=fill, outline="", tags="card")
