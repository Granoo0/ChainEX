"""
dwm_effects.py — Windows Desktop Window Manager (DWM) visual polish.

Applies dark-mode title bar, custom caption/border/text colours, and rounded
window corners via DwmSetWindowAttribute.  Every call is wrapped in a broad
try/except so the app degrades gracefully on Windows 7 / Wine / VMs where
these attributes are unsupported.

Supported from:
  - Dark title bar (attr 20)       : Windows 10 build 17763+
  - Caption / border colour (34/35): Windows 11 22000+
  - Rounded corners (attr 33)      : Windows 11 22000+
  - Mica / Acrylic backdrop (38)   : Windows 11 22621+

Nothing raises — caller always gets silent success or silent skip.
"""

import ctypes
import ctypes.wintypes as wt
import tkinter as tk
from typing import Optional

# ── DWM attribute constants ────────────────────────────────────────────────────
DWMWA_USE_IMMERSIVE_DARK_MODE: int = 20   # BOOL — dark title bar
DWMWA_WINDOW_CORNER_PREFERENCE: int = 33  # DWORD — rounded corners
DWMWA_BORDER_COLOR: int = 34              # COLORREF — window border colour
DWMWA_CAPTION_COLOR: int = 35             # COLORREF — title bar fill
DWMWA_TEXT_COLOR: int = 36               # COLORREF — title bar text
DWMWA_SYSTEMBACKDROP_TYPE: int = 38      # DWORD — Mica/Acrylic

# DWMWA_WINDOW_CORNER_PREFERENCE values
DWMWCP_DEFAULT: int = 0   # OS default
DWMWCP_DONOTROUND: int = 1
DWMWCP_ROUND: int = 2     # Rounded
DWMWCP_ROUNDSMALL: int = 3

# DWMWA_SYSTEMBACKDROP_TYPE values
DWMSBT_AUTO: int = 0
DWMSBT_DISABLE: int = 1   # Solid colour only
DWMSBT_MAINWINDOW: int = 2  # Mica (main window, Win11 22H2)
DWMSBT_TRANSIENTWINDOW: int = 3  # Acrylic
DWMSBT_TABBEDWINDOW: int = 4    # Mica Alt

# Sentinel: DWM will use its own default border colour
DWMWA_COLOR_DEFAULT: int = 0xFFFFFFFF
DWMWA_COLOR_NONE: int = 0xFFFFFFFE


# ── Helpers ────────────────────────────────────────────────────────────────────

def _colorref(hex_color: str) -> int:
    """Convert a #RRGGBB hex string to a Windows COLORREF (0x00BBGGRR)."""
    h = hex_color.lstrip("#")
    r = int(h[0:2], 16)
    g = int(h[2:4], 16)
    b = int(h[4:6], 16)
    return (b << 16) | (g << 8) | r


def get_hwnd(widget: tk.Widget) -> Optional[int]:
    """
    Retrieve the Win32 top-level (decoration frame) HWND for a Tk widget.

    Tkinter window structure on Windows:
    ::

        Desktop
          └── TkTopLevel  ← the decorated frame with title bar (DWM target)
                └── TkChild  ← the actual drawing surface (winfo_id returns this)

    ``widget.wm_frame()`` is the official Tkinter API that returns the HWND
    of the outer frame as a hex string (e.g. ``"0x00123ABC"``).  This is
    exactly the window handle that DWM attributes must be applied to.

    Falls back to ``GetParent(winfo_id())`` if ``wm_frame`` is unavailable,
    and finally to ``winfo_id()`` itself as a last resort.

    Returns None only if all three methods raise an exception.
    """
    # Primary: wm_frame() is the documented Tkinter way to get the frame HWND
    try:
        frame = widget.wm_frame()          # type: ignore[attr-defined]
        if frame:
            return int(frame, 16)
    except Exception:
        pass

    # Fallback 1: GetParent of the inner child window
    try:
        child_hwnd: int = widget.winfo_id()
        parent_hwnd: int = ctypes.windll.user32.GetParent(child_hwnd)
        if parent_hwnd:
            return parent_hwnd
        # GetParent returned 0 — the child IS the top-level; use it directly
        return child_hwnd if child_hwnd else None
    except Exception:
        pass

    return None


def _set_attr_bool(hwnd: int, attr: int, value: bool) -> bool:
    """Call DwmSetWindowAttribute with a BOOL value. Returns True on success."""
    try:
        flag = ctypes.c_int(1 if value else 0)
        hr = ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, attr,
            ctypes.byref(flag),
            ctypes.sizeof(flag),
        )
        return hr == 0  # S_OK
    except Exception:
        return False


def _set_attr_dword(hwnd: int, attr: int, value: int) -> bool:
    """Call DwmSetWindowAttribute with a DWORD value. Returns True on success."""
    try:
        val = ctypes.c_uint32(value)
        hr = ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, attr,
            ctypes.byref(val),
            ctypes.sizeof(val),
        )
        return hr == 0
    except Exception:
        return False


# ── Public API ─────────────────────────────────────────────────────────────────

def apply_dark_titlebar(hwnd: int, dark: bool = True) -> bool:
    """
    Enable or disable the dark (immersive) title bar.

    Args:
        hwnd: Win32 window handle.
        dark: True = dark title bar; False = light.

    Returns:
        True if DWM accepted the attribute.
    """
    return _set_attr_bool(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, dark)


def apply_rounded_corners(hwnd: int, mode: int = DWMWCP_ROUND) -> bool:
    """
    Set the window corner rounding preference (Windows 11+).

    Args:
        hwnd: Win32 window handle.
        mode: One of DWMWCP_DEFAULT, DWMWCP_DONOTROUND, DWMWCP_ROUND,
              DWMWCP_ROUNDSMALL.

    Returns:
        True if DWM accepted the attribute.
    """
    return _set_attr_dword(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, mode)


def apply_border_color(hwnd: int, hex_color: str) -> bool:
    """
    Set the window border (accent) colour (Windows 11 22000+).

    Args:
        hwnd:      Win32 window handle.
        hex_color: Colour as ``"#RRGGBB"`` string, or ``""`` to restore default.

    Returns:
        True if DWM accepted the attribute.
    """
    colorref = _colorref(hex_color) if hex_color else DWMWA_COLOR_DEFAULT
    return _set_attr_dword(hwnd, DWMWA_BORDER_COLOR, colorref)


def apply_caption_color(hwnd: int, hex_color: str) -> bool:
    """
    Set the title bar background colour (Windows 11 22000+).

    Args:
        hwnd:      Win32 window handle.
        hex_color: Colour as ``"#RRGGBB"`` string.

    Returns:
        True if DWM accepted the attribute.
    """
    return _set_attr_dword(hwnd, DWMWA_CAPTION_COLOR, _colorref(hex_color))


def apply_text_color(hwnd: int, hex_color: str) -> bool:
    """
    Set the title bar text colour (Windows 11 22000+).

    Args:
        hwnd:      Win32 window handle.
        hex_color: Colour as ``"#RRGGBB"`` string.

    Returns:
        True if DWM accepted the attribute.
    """
    return _set_attr_dword(hwnd, DWMWA_TEXT_COLOR, _colorref(hex_color))


def apply_backdrop(hwnd: int, backdrop_type: int = DWMSBT_MAINWINDOW) -> bool:
    """
    Set the system backdrop effect (Mica / Acrylic — Windows 11 22H2+).

    Args:
        hwnd:          Win32 window handle.
        backdrop_type: One of the DWMSBT_* constants.

    Returns:
        True if DWM accepted the attribute.
    """
    return _set_attr_dword(hwnd, DWMWA_SYSTEMBACKDROP_TYPE, backdrop_type)


def apply_theme(
    hwnd: int,
    *,
    caption_color: str = "#131829",
    border_color: str  = "#22D3EE",
    text_color: str    = "#E2E8F0",
    dark: bool         = True,
    rounded: bool      = True,
) -> dict[str, bool]:
    """
    Convenience wrapper — apply the full ChainEX visual theme in one call.

    All individual calls degrade silently; the returned dict reports which
    attributes were successfully accepted by DWM.

    Args:
        hwnd:          Win32 HWND of the top-level window.
        caption_color: Title bar background (#RRGGBB).
        border_color:  Window border accent (#RRGGBB).
        text_color:    Title bar text colour (#RRGGBB).
        dark:          True to request dark title bar controls.
        rounded:       True to request rounded window corners.

    Returns:
        Dict mapping attribute name → bool (True = DWM accepted).
    """
    results: dict[str, bool] = {}
    results["dark_titlebar"] = apply_dark_titlebar(hwnd, dark)
    results["caption_color"] = apply_caption_color(hwnd, caption_color)
    results["border_color"]  = apply_border_color(hwnd, border_color)
    results["text_color"]    = apply_text_color(hwnd, text_color)
    if rounded:
        results["corners"] = apply_rounded_corners(hwnd, DWMWCP_ROUND)
    return results
