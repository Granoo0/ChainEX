"""
launcher.py — ChainEX GUI Launcher  v2
"""

import collections
import copy
import json
import logging
import os
import shutil
import sys
import threading
import time
import queue
import datetime
import cv2
import numpy as np
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk, simpledialog
from pathlib import Path
from PIL import Image, ImageTk, ImageGrab
import ctypes
import win32gui
from pynput import keyboard as pynput_kb, mouse as pynput_mouse

# ── FIX BLURRINESS & TASKBAR ICON ──────────────────────────────────────────
try:
    myappid = 'chainex.macro.automation.v2'
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
except (OSError, AttributeError):
    pass

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except (OSError, AttributeError):
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except (OSError, AttributeError):
        pass

# ── Paths ───────────────────────────────────────────────────────────────────
_HERE = Path(__file__).resolve().parent
(_HERE / "profiles").mkdir(exist_ok=True)
(_HERE / "templates").mkdir(exist_ok=True)
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

_HISTORY_FILE = _HERE / "session_history.json"   # persistent session log

from config_loader import load_config, list_profiles, load_profile
from paths   import PACKAGE_ROOT, apply_path_resolution, make_paths_relative
from version import VERSION as _VERSION
from bot_logger import configure_logging_from_config, get_logger

_log = get_logger("UI")

_BOOT_CFG = apply_path_resolution(load_config(str(PACKAGE_ROOT / "config.json")))

# ── Purge log file + rotated backups on every startup (fresh session) ─────────
# Primary cleanup happens in _on_close; this is the crash-recovery fallback.
log_path    = Path(_BOOT_CFG.get("logging", {}).get("log_file", str(_HERE / "bot.log")))
_log_backup = int(_BOOT_CFG.get("logging", {}).get("backup_count", 3))
for _log_n in range(_log_backup + 1):
    _lp = Path(str(log_path) + ("" if _log_n == 0 else f".{_log_n}"))
    try:
        if _lp.exists():
            _lp.unlink()
    except Exception:
        pass

configure_logging_from_config(_BOOT_CFG["logging"])

from window_ctrl      import WindowController
from bot_engine       import BotEngine
from image_recognizer import ImageRecognizer
from remote_server    import RemoteDashboard
from dwm_effects      import apply_theme as _dwm_apply_theme, get_hwnd as _dwm_get_hwnd
from ui_kit           import AnimatedButton

def _fmt_dur(seconds: float) -> str:
    """Format seconds as mm:ss or h:mm for chart axis labels."""
    s = int(seconds)
    if s < 3600:
        return f"{s // 60}:{s % 60:02d}"
    return f"{s // 3600}h{(s % 3600) // 60:02d}"

# ──────────────────────────────────────────────────────────────────────────────
#  CHAINEX THEME  v3  ·  Modern Slate (Tailwind-inspired, WCAG AA contrast)
# ──────────────────────────────────────────────────────────────────────────────
_BG        = "#0A0E1A"   # deep navy background
_BG_CARD   = "#131829"   # card / panel surface
_BG_HL     = "#1E2538"   # hover / entry field background
_BG_INSET  = "#0D111E"   # inset / inner panel
_ACCENT    = "#22D3EE"   # cyan-400 — primary accent (softer, modern)
_ACCENT_MU = "#0E7490"   # cyan-700 — muted accent for backgrounds
_DANGER    = "#F43F5E"   # rose-500 — stop / danger
_SUCCESS   = "#10B981"   # emerald-500 — running / success
_WARN      = "#FACC15"   # yellow-400 — warning / WAIT (clearly yellow)
_ORANGE    = "#FF8C00"   # vivid orange — POS steps (clearly orange)
_PURPLE    = "#A78BFA"   # violet-400 — KEY steps
# Semantic aliases (kept for backward-compatible call-sites)
_RED       = _DANGER
_GREEN     = _SUCCESS
_YELLOW    = _WARN
# Pulse animation — dim variant of _SUCCESS used for the running-dot blink
_PULSE_OFF = "#0D6B4E"   # emerald-900 — dimmed dot (alternates with _SUCCESS)
_TEXT      = "#E2E8F0"   # slate-200 — primary text
_TEXT_MID  = "#94A3B8"   # slate-400 — secondary (AA-compliant on _BG_CARD)
_TEXT_DIM  = "#64748B"   # slate-500 — dim / placeholder
_BORDER    = "#1E293B"   # slate-800 — subtle border
_BORDER_LT = "#334155"   # slate-700 — divider / lighter border

# Macro editor row tones (local to step rows)
_ROW_A     = "#131829"   # even row (matches card surface)
_ROW_B     = "#1A2034"   # odd row — clearly distinct
_ENTRY_BG  = "#1E2538"   # entry field fill
_ENTRY_BD  = "#334155"   # entry border (idle)
_ENTRY_FC  = "#22D3EE"   # entry focus ring (= _ACCENT)

# Toolbar muted badge backgrounds (deep tinted, harmonised with new accents)
_TB_POS    = "#2A1808"   # deep orange tint
_TB_WAIT   = "#2A2608"   # deep yellow tint
_TB_KEY    = "#1F1438"   # deep violet tint
_TB_TPL    = "#08252E"   # deep cyan tint
_TB_TRIG   = "#2A1A08"   # deep amber-orange tint
_TB_REC    = "#2A0A14"   # deep rose tint

# ── Layout constants ─────────────────────────────────────────────────────────
_PAD       = 14    # standard outer padding (px)
_TOPBAR_H  = 46    # top bar height (px)

# ── Log level colors (harmonised with theme) ─────────────────────────────────
_LOG_DEBUG   = "#64748B"   # slate-500 for DEBUG
_LOG_INFO    = "#22D3EE"   # cyan for INFO (= _ACCENT)
_LOG_WARNING = "#FACC15"   # yellow for WARNING
_LOG_ERROR   = "#F43F5E"   # rose for ERROR

# ── Config field types for validation (Fix #6) ──────────────────────────────
_NUMERIC_CFG_KEYS: dict[str, type] = {
    "vision.match_threshold":           float,
    "timing.cycle_debug_interval_ms":   int,
    "timing.max_runtime_minutes":       float,
    "timing.reconnect_timeout_s":       float,
    "macro.wait_timeout_s":             float,
    "macro.playback_speed":             float,
    "macro.click_delay_ms":             int,
    "input.random_click_offset_px":     int,
    "remote.port":                      int,
}

# ── Hotkey modifier-key sets ────────────────────────────────────────────────
_HK_MOD_KEYS = {
    pynput_kb.Key.ctrl,    pynput_kb.Key.ctrl_l,  pynput_kb.Key.ctrl_r,
    pynput_kb.Key.alt,     pynput_kb.Key.alt_l,   pynput_kb.Key.alt_r,
    pynput_kb.Key.alt_gr,
    pynput_kb.Key.shift,   pynput_kb.Key.shift_l, pynput_kb.Key.shift_r,
    pynput_kb.Key.cmd,     pynput_kb.Key.cmd_l,   pynput_kb.Key.cmd_r,
}
_HK_MOD_NAME: dict = {
    pynput_kb.Key.ctrl:    "ctrl",  pynput_kb.Key.ctrl_l:  "ctrl",  pynput_kb.Key.ctrl_r:  "ctrl",
    pynput_kb.Key.alt:     "alt",   pynput_kb.Key.alt_l:   "alt",   pynput_kb.Key.alt_r:   "alt",
    pynput_kb.Key.alt_gr:  "alt",
    pynput_kb.Key.shift:   "shift", pynput_kb.Key.shift_l: "shift", pynput_kb.Key.shift_r: "shift",
    pynput_kb.Key.cmd:     "win",   pynput_kb.Key.cmd_l:   "win",   pynput_kb.Key.cmd_r:   "win",
}

_F_TITLE = ("Segoe UI", 13, "bold")
_F_HEAD  = ("Segoe UI", 10, "bold")
_F_BODY  = ("Segoe UI", 10)
_F_SMALL = ("Segoe UI",  8)
_F_LABEL = ("Segoe UI",  8, "bold")
_F_MONO  = ("Consolas", 10)
_F_MONOS = ("Consolas",  9)
_F_MONOB = ("Consolas", 11, "bold")


# ──────────────────────────────────────────────────────────────────────────────
#  HoverButton — animated flat button (backed by ui_kit.AnimatedButton)
#
#  All existing call-sites use HoverButton unchanged; the class now delivers
#  smooth 140 ms colour transitions instead of instant swaps.
# ──────────────────────────────────────────────────────────────────────────────
class HoverButton(AnimatedButton):
    """Backward-compatible alias for AnimatedButton with ChainEX defaults."""

    def __init__(self, master, hover_bg: str = _ACCENT,
                 hover_fg: str = "#000000", **kw):
        super().__init__(master, hover_bg=hover_bg, hover_fg=hover_fg, **kw)


# ──────────────────────────────────────────────────────────────────────────────
#  Tooltip — lightweight hover tooltip for any widget
# ──────────────────────────────────────────────────────────────────────────────
class Tooltip:
    """Show a small popup label after a short hover delay."""
    _DELAY_MS = 500

    def __init__(self, widget: tk.Widget, text: str) -> None:
        self._widget  = widget
        self._text    = text
        self._job: str | None  = None
        self._tip: tk.Toplevel | None = None
        widget.bind("<Enter>",    self._on_enter,  add="+")
        widget.bind("<Leave>",    self._on_leave,  add="+")
        widget.bind("<Button-1>", self._on_leave,  add="+")

    def _on_enter(self, _: tk.Event) -> None:
        self._job = self._widget.after(self._DELAY_MS, self._show)

    def _on_leave(self, _: tk.Event) -> None:
        if self._job:
            self._widget.after_cancel(self._job)
            self._job = None
        if self._tip:
            self._tip.destroy()
            self._tip = None

    def _show(self) -> None:
        x = self._widget.winfo_rootx() + self._widget.winfo_width() // 2
        y = self._widget.winfo_rooty() + self._widget.winfo_height() + 4
        self._tip = tk.Toplevel(self._widget)
        self._tip.overrideredirect(True)
        self._tip.attributes("-topmost", True)
        tk.Label(
            self._tip, text=self._text,
            font=("Segoe UI", 8), fg=_TEXT, bg="#1E2538",
            relief="flat", padx=8, pady=4,
        ).pack()
        self._tip.geometry(f"+{x}+{y}")


# ──────────────────────────────────────────────────────────────────────────────
#  GPanelApp
# ──────────────────────────────────────────────────────────────────────────────
class GPanelApp(tk.Tk):

    # ── Init ─────────────────────────────────────────────────────────────────
    def __init__(self) -> None:
        super().__init__()
        # ── Frameless window — native chrome removed; Win32 re-applied later ──
        self.overrideredirect(True)
        self.title("ChainEX")
        self.geometry("1140x750")
        self.minsize(960, 620)
        self.configure(bg=_BG)

        ico_p = _HERE / "chainex_icon.png"
        if ico_p.exists():
            self._icon_img = ImageTk.PhotoImage(
                Image.open(ico_p).resize((64, 64), Image.LANCZOS))
            self.iconphoto(False, self._icon_img)

        self._cfg              = apply_path_resolution(load_config(str(PACKAGE_ROOT / "config.json")))
        self._is_running       = False
        self._stop_event       = threading.Event()
        self._frame_queue      = queue.Queue(maxsize=1)
        self._log_pos          = 0
        self._cfg_vars: dict   = {}
        self._tk_img:  ImageTk.PhotoImage | None = None
        self._tk_img2: ImageTk.PhotoImage | None = None
        self._hk_listener: pynput_kb.Listener | None = None
        self._hk_mods: set[str] = set()
        self._hotkey_capture_active: bool = False
        self._hotkey_capture_cb: "callable | None" = None
        self._hotkey_capture_cancel_cb: "callable | None" = None
        self._stop_after_loop_event: threading.Event  = threading.Event()
        self._pause_event:           threading.Event  = threading.Event()
        self._stats_queue:           queue.Queue      = queue.Queue(maxsize=2)
        self._secondary_pending:     bool             = False
        self._secondary_profile:     str              = ""
        self._secondary_one_shot:    bool             = False
        self._primary_cfg_backup:    dict | None      = None
        self._stat_vals:             dict             = {}   # tag → value Label
        self._sec_prof_var:          tk.StringVar | None = None
        self._reconnect_deadline:    float            = 0.0
        self._session_start:         float            = 0.0 # session timer
        self._template_stems_cache: set[str] | None = None
        self._log_filter:  str = "ALL"   # ALL | DEBUG | INFO | WARNING | ERROR
        self._tpl_thumb_popup: tk.Toplevel | None = None
        self._stop_reason: str = ""      # Set by engine thread; read by _stop_bot
        self._slider_save_id: int | None = None  # Debounce handle for slider writes
        self._active_profile: str | None = None  # Filename of the currently-loaded profile

        # Undo / Redo
        self._undo_stack: collections.deque = collections.deque(maxlen=40)
        self._redo_stack: collections.deque = collections.deque(maxlen=40)

        # Step clipboard (right-click copy/cut/paste)
        self._clipboard_step = None  # holds a deep-copied step dict/str

        # Last known stats (populated by _poll_stats; read by _stop_bot for history)
        self._last_stats: dict = {}

        # Widget references
        self._feed_canvas:    tk.Canvas                = None  # type: ignore
        self._log_box:        scrolledtext.ScrolledText = None  # type: ignore
        self._btn_main_start: tk.Button                = None  # type: ignore
        self._btn_main_stop:  tk.Button                = None  # type: ignore
        self._btn_pause:      tk.Button                = None  # type: ignore
        self._lbl_status:     tk.Label                 = None  # type: ignore
        self._lbl_cycle:      tk.Label                 = None  # type: ignore
        self._profile_list:   tk.Listbox               = None  # type: ignore
        self._btn_subloop:    tk.Button                = None  # type: ignore
        self._sb_cycle:       tk.Label                 = None  # type: ignore
        self._pb_val_lbl:     tk.Label                 = None  # type: ignore
        self._btn_profile_save: tk.Button              = None  # type: ignore
        self._lbl_active_prof:  tk.Label               = None  # type: ignore
        self._btn_undo:           tk.Button              = None  # type: ignore
        self._btn_redo:           tk.Button              = None  # type: ignore
        self._history_rows_frame: tk.Frame             = None  # type: ignore
        self._lbl_est_time:       tk.Label              = None  # type: ignore
        self._lbl_step_count:     tk.Label              = None  # type: ignore
        self._tpl_rows_frame:     tk.Frame              = None  # type: ignore

        # Drag-and-drop reordering
        self._drag_idx:       int | None    = None
        self._drag_drop_line: tk.Frame | None = None
        self._step_row_frames: list         = []

        # Step highlight (written by engine thread, read by poll)
        self._active_step_ref:  list[int]  = [-1]   # [0] = current engine step index
        self._step_active_bars: list       = []      # (bar_widget, normal_bg) per row

        # Macro step search/filter
        self._macro_search_var: tk.StringVar | None = None
        self._lbl_filter_count: tk.Label | None     = None

        # Buttons that must be disabled while the bot is running
        self._btn_macro_clear:  tk.Widget | None    = None
        self._btn_macro_import: tk.Widget | None    = None

        # History chart canvas + cached records for resize-redraws
        self._hist_chart_canvas: tk.Canvas | None   = None
        self._hist_chart_records: list              = []

        # Remote dashboard
        self._remote_dash: RemoteDashboard | None   = None
        self._lbl_remote_url: tk.Label | None       = None

        # Custom tab bar state
        self._active_tab_idx: int         = 0
        self._tab_panes:      list        = []   # [frame, ...]  one per tab
        self._tab_activate:   list        = []   # [callable, ...] highlight tab btn
        self._tab_deactivate: list        = []   # [callable, ...] dim tab btn
        self._btn_win_max:    tk.Label | None = None  # maximize label-button

        # Scheduler
        self._sched_enabled_var: tk.BooleanVar | None = None
        self._sched_start_var:   tk.StringVar  | None = None
        self._sched_stop_var:    tk.StringVar  | None = None
        self._sched_lbl:         tk.Label | None      = None
        self._sched_poll_job:    int | None           = None  # after() handle — prevents double chains

        # DWM / animation
        self._pulse_job: int | None = None         # after() handle for status-dot pulse
        self._status_clear_job: int | None = None  # after() handle for timed status resets

        self._setup_styles()
        self._build_topbar()       # top — pack first
        self._build_statusbar()    # bottom — pack second (before expand body)

        body = tk.Frame(self, bg=_BG)
        body.pack(fill="both", expand=True)

        self._build_sidebar(body)

        right = tk.Frame(body, bg=_BG)
        right.pack(side="right", fill="both", expand=True, padx=(0, 0), pady=0)

        # ── Custom tab bar ────────────────────────────────────────────────────
        self._build_tabbar(right)

        # ── Content pane ──────────────────────────────────────────────────────
        self._tab_content = tk.Frame(right, bg=_BG)
        self._tab_content.pack(fill="both", expand=True, padx=14, pady=(0, 14))

        # Create each tab frame as a child of the content area (NOT a Notebook)
        self._tab_dash      = tk.Frame(self._tab_content, bg=_BG)
        self._tab_macro     = tk.Frame(self._tab_content, bg=_BG)
        self._tab_profiles  = tk.Frame(self._tab_content, bg=_BG)
        self._tab_settings  = tk.Frame(self._tab_content, bg=_BG)
        self._tab_history   = tk.Frame(self._tab_content, bg=_BG)
        self._tab_templates = tk.Frame(self._tab_content, bg=_BG)

        self._tab_panes = [
            self._tab_dash, self._tab_macro, self._tab_profiles,
            self._tab_settings, self._tab_history, self._tab_templates,
        ]
        # Show only the first tab; all others are hidden
        self._tab_dash.pack(fill="both", expand=True)

        self._build_dashboard()
        self._build_macro_editor()
        self._build_profiles_tab()
        self._build_settings()
        self._build_history_tab()
        self._build_templates_tab()

        # Global undo / redo keyboard shortcuts
        self.bind_all("<Control-z>", lambda e: self._undo())
        self.bind_all("<Control-y>", lambda e: self._redo())

        self._refresh_profiles()
        self._refresh_macro_list()
        self._setup_hotkeys()
        self._poll_log()
        self._poll_frame()
        self._poll_stats()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # ── Remote dashboard ──────────────────────────────────────────────────
        self._start_remote_dashboard()

        # ── Frameless Win32 chrome + DWM polish ───────────────────────────────
        # Both calls are deferred: the HWND must be live and painted first.
        self.after(80,  self._setup_frameless)
        self.after(200, self._apply_dwm_effects)

    # ── Styles ───────────────────────────────────────────────────────────────
    def _setup_styles(self) -> None:
        """Apply ttk style overrides to match the ChainEX dark theme."""
        s = ttk.Style()
        s.theme_use("clam")
        s.configure("TNotebook",
                    background=_BG_CARD, borderwidth=0, tabmargins=[0, 0, 0, 0])
        s.configure("TNotebook.Tab",
                    background=_BG_CARD,
                    foreground=_TEXT_MID,
                    padding=[16, 8],
                    font=_F_HEAD,
                    borderwidth=0,
                    focuscolor=_BG_CARD)
        s.map("TNotebook.Tab",
              background=[("selected", _BG_HL),  ("active", _BG_HL)],
              foreground=[("selected", _ACCENT),  ("active", _TEXT)])
        s.configure("Vertical.TScrollbar",
                    gripcount=0,
                    background=_BORDER_LT,
                    darkcolor=_BG_INSET,
                    lightcolor=_BG_INSET,
                    troughcolor=_BG_INSET,
                    borderwidth=0,
                    arrowsize=0,
                    width=8)
        s.map("Vertical.TScrollbar",
              background=[("active", _ACCENT_MU), ("pressed", _ACCENT)])

    # ── DWM Effects ───────────────────────────────────────────────────────────

    def _apply_dwm_effects(self) -> None:
        """Apply DWM visual polish for the frameless window.

        Because the native title bar is removed (overrideredirect), caption /
        border / text-colour attributes have no visible chrome to affect.
        We still apply:
          - Dark immersive mode (attr 20) — colours Alt-Tab thumbnail border
            and the window drop shadow on Win 10/11.
          - Rounded corners (attr 33) — rounds the shadow outline on Win 11.

        Every attribute call degrades silently on unsupported OS versions.
        """
        hwnd = _dwm_get_hwnd(self)
        if not hwnd:
            _log.warning("DWM: could not obtain HWND — visual polish skipped")
            return
        from dwm_effects import apply_dark_titlebar, apply_rounded_corners
        dark    = apply_dark_titlebar(hwnd, True)
        rounded = apply_rounded_corners(hwnd)
        _log.info(
            "DWM frameless polish (HWND=0x%X): dark=%s rounded=%s",
            hwnd, dark, rounded,
        )

    # ── Status-dot pulse animation ─────────────────────────────────────────────

    def _pulse_status(self) -> None:
        """Blink the status dot while the bot is actively running.

        Alternates the label foreground between bright emerald (_SUCCESS) and
        a dimmed variant (_PULSE_OFF) every 600 ms.  Stops automatically when
        the bot stops or is paused.
        """
        if not self._is_running or self._pause_event.is_set():
            # Ensure we land on the bright colour when stopped/paused
            # so the label is consistent with whatever _stop_bot / _toggle_pause set.
            self._pulse_job = None
            return
        current_fg = self._lbl_status.cget("fg")
        next_fg    = _PULSE_OFF if current_fg == _SUCCESS else _SUCCESS
        self._lbl_status.config(fg=next_fg)
        self._pulse_job = self.after(600, self._pulse_status)

    # ── Top Bar (frameless custom title bar) ──────────────────────────────────
    def _build_topbar(self) -> None:
        """Custom frameless title bar: logo · status · window controls."""
        bar = tk.Frame(self, bg=_BG_CARD, height=_TOPBAR_H)
        bar.pack(fill="x", side="top")
        bar.pack_propagate(False)
        # The whole bar is a drag zone; individual buttons stop propagation.
        bar.bind("<ButtonPress-1>",  self._titlebar_drag)
        bar.bind("<Double-Button-1>", lambda e: self._win_toggle_max())

        # ── Left: icon + brand name ────────────────────────────────────────
        left = tk.Frame(bar, bg=_BG_CARD)
        left.pack(side="left", fill="y", padx=(14, 0))
        left.bind("<ButtonPress-1>", self._titlebar_drag)

        ico_p = _HERE / "chainex_tray.png"
        if ico_p.exists():
            try:
                img = Image.open(ico_p).resize((24, 24), Image.LANCZOS)
                self._top_ico = ImageTk.PhotoImage(img)
                ico_lbl = tk.Label(left, image=self._top_ico, bg=_BG_CARD)
                ico_lbl.pack(side="left", padx=(0, 8))
                ico_lbl.bind("<ButtonPress-1>", self._titlebar_drag)
            except Exception:
                pass

        for txt, fg in (("Chain", _TEXT), ("EX", _ACCENT)):
            lbl = tk.Label(left, text=txt,
                           font=("Consolas", 13, "bold"), fg=fg, bg=_BG_CARD)
            lbl.pack(side="left")
            lbl.bind("<ButtonPress-1>", self._titlebar_drag)

        sub = tk.Label(left, text="  ·  Macro Automation",
                       font=_F_BODY, fg=_TEXT_MID, bg=_BG_CARD)
        sub.pack(side="left")
        sub.bind("<ButtonPress-1>", self._titlebar_drag)

        # ── Right: status · version · window controls ──────────────────────
        right = tk.Frame(bar, bg=_BG_CARD)
        right.pack(side="right", fill="y")

        # Window controls — each returns "break" to stop drag propagation
        def _ctrl(parent, text, normal_fg, hover_bg, hover_fg, action, width=46):
            lbl = tk.Label(parent, text=text,
                           font=("Segoe UI", 10), fg=normal_fg,
                           bg=_BG_CARD, width=0, padx=0, pady=0,
                           cursor="hand2")
            lbl.pack(side="right", fill="y", ipadx=width // 3, ipady=6)
            lbl.bind("<Button-1>",  lambda e, fn=action: (fn(), "break")[1])
            lbl.bind("<Enter>",     lambda e, l=lbl, hb=hover_bg, hf=hover_fg:
                                        l.config(bg=hb, fg=hf))
            lbl.bind("<Leave>",     lambda e, l=lbl: l.config(bg=_BG_CARD, fg=normal_fg))
            return lbl

        _ctrl(right, "✕", _TEXT_MID, _DANGER,  "#ffffff", self._win_close,   width=46)
        self._btn_win_max = _ctrl(right, "□", _TEXT_MID, _BG_HL, _TEXT,
                                  self._win_toggle_max, width=40)
        _ctrl(right, "—", _TEXT_MID, _BG_HL,   _TEXT,    self._win_minimize, width=40)

        # Thin divider before controls
        tk.Frame(right, bg=_BORDER_LT, width=1).pack(side="right", fill="y", pady=8)

        # Version + status (draggable)
        ver = tk.Label(right, text=_VERSION,
                       font=_F_MONOS, fg=_TEXT_DIM, bg=_BG_CARD)
        ver.pack(side="right", padx=(0, 14))
        ver.bind("<ButtonPress-1>", self._titlebar_drag)

        self._lbl_status = tk.Label(right, text="● IDLE",
                                    font=("Segoe UI", 9, "bold"),
                                    fg=_TEXT_MID, bg=_BG_CARD)
        self._lbl_status.pack(side="right", padx=(0, 8))
        self._lbl_status.bind("<ButtonPress-1>", self._titlebar_drag)

        # Bottom separator
        tk.Frame(self, bg=_BORDER_LT, height=1).pack(fill="x", side="top")

    # ── Frameless Win32 chrome ────────────────────────────────────────────────

    def _setup_frameless(self) -> None:
        """Apply Win32 styles so the overrideredirect window behaves like a
        real app window: native resize handles, drop shadow, taskbar button,
        and Alt-Tab entry — without any native title bar.

        Uses win32gui (pywin32) for GetWindowLong/SetWindowLong to avoid the
        ctypes c_long overflow that occurs with large DWORD style values on
        Python 3.12+.
        """
        try:
            hwnd = int(self.wm_frame(), 16)

            GWL_STYLE   = -16
            GWL_EXSTYLE = -20
            WS_THICKFRAME  = 0x00040000   # resize handles + DWM shadow
            WS_MINIMIZEBOX = 0x00020000
            WS_MAXIMIZEBOX = 0x00010000
            WS_SYSMENU     = 0x00080000
            WS_POPUP       = 0x80000000
            WS_EX_APPWINDOW  = 0x00040000  # taskbar + Alt-Tab entry
            WS_EX_TOOLWINDOW = 0x00000080  # remove this flag

            # pywin32 handles unsigned DWORDs correctly — no overflow
            style = win32gui.GetWindowLong(hwnd, GWL_STYLE)
            style = (style & ~WS_POPUP) | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX | WS_SYSMENU
            win32gui.SetWindowLong(hwnd, GWL_STYLE, style)

            ex = win32gui.GetWindowLong(hwnd, GWL_EXSTYLE)
            ex = (ex | WS_EX_APPWINDOW) & ~WS_EX_TOOLWINDOW
            win32gui.SetWindowLong(hwnd, GWL_EXSTYLE, ex)

            # Force frame recalculation so shadow + resize appear immediately
            SWP_FLAGS = 0x0027   # NOMOVE | NOSIZE | NOZORDER | FRAMECHANGED
            ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, SWP_FLAGS)
            _log.debug("Frameless Win32 chrome applied (HWND=0x%X)", hwnd)
        except Exception as exc:
            _log.warning("_setup_frameless failed: %s", exc)

    def _titlebar_drag(self, event: tk.Event) -> None:
        """Send WM_NCLBUTTONDOWN/HTCAPTION so Windows handles drag natively.
        Gives proper snap-zone behaviour and multi-monitor support for free.

        Uses PostMessageW (fire-and-forget) instead of SendMessageW to avoid
        the GIL violation that occurs on Python 3.12+ when a blocking Win32
        message pump call is made from inside a tkinter event callback.
        """
        try:
            hwnd = int(self.wm_frame(), 16)
            ctypes.windll.user32.ReleaseCapture()
            # PostMessageW is non-blocking: no GIL issue on Python 3.12+
            ctypes.windll.user32.PostMessageW(hwnd, 0x00A1, 2, 0)
            # 0x00A1 = WM_NCLBUTTONDOWN,  2 = HTCAPTION
        except Exception:
            pass

    def _win_minimize(self) -> None:
        """Minimize the window via Win32 (taskbar + keyboard shortcuts work)."""
        try:
            ctypes.windll.user32.ShowWindow(int(self.wm_frame(), 16), 6)
        except Exception:
            self.iconify()

    def _win_toggle_max(self) -> None:
        """Toggle between maximized and restored states."""
        try:
            hwnd = int(self.wm_frame(), 16)
            if ctypes.windll.user32.IsZoomed(hwnd):
                ctypes.windll.user32.ShowWindow(hwnd, 9)   # SW_RESTORE
                if self._btn_win_max:
                    self._btn_win_max.config(text="□")
            else:
                ctypes.windll.user32.ShowWindow(hwnd, 3)   # SW_MAXIMIZE
                if self._btn_win_max:
                    self._btn_win_max.config(text="❐")
        except Exception:
            pass

    def _win_close(self) -> None:
        """Route through the normal close handler (prompts if bot is running)."""
        self._on_close()

    # ── Custom Tab Bar ────────────────────────────────────────────────────────

    # Tab definitions: (icon, label, name-for-refresh-check)
    _TAB_DEFS = [
        ("⊞", "Dashboard",    "DASHBOARD"),
        ("⚡", "Macro",        "MACRO"),
        ("◆", "Profiles",     "PROFILES"),
        ("⚙", "Settings",     "SETTINGS"),
        ("⏱", "History",      "HISTORY"),
        ("⧉", "Templates",    "TEMPLATES"),
    ]

    def _build_tabbar(self, parent: tk.Frame) -> None:
        """Build the horizontal custom tab bar above the content pane."""
        bar = tk.Frame(parent, bg=_BG_CARD)
        bar.pack(fill="x", side="top")

        # Thin top accent line (web-app style)
        tk.Frame(bar, bg=_ACCENT, height=2).pack(fill="x", side="top")

        tabs_row = tk.Frame(bar, bg=_BG_CARD)
        tabs_row.pack(fill="x", side="top")

        self._tab_activate   = []
        self._tab_deactivate = []

        for idx, (icon, label, _) in enumerate(self._TAB_DEFS):
            btn_frame = tk.Frame(tabs_row, bg=_BG_CARD, cursor="hand2")
            btn_frame.pack(side="left")

            inner = tk.Frame(btn_frame, bg=_BG_CARD)
            inner.pack(padx=16, pady=(8, 6))

            ico_lbl  = tk.Label(inner, text=icon,  font=("Segoe UI", 9),
                                fg=_TEXT_MID, bg=_BG_CARD)
            ico_lbl.pack(side="left", padx=(0, 5))

            txt_lbl  = tk.Label(inner, text=label, font=_F_HEAD,
                                fg=_TEXT_MID, bg=_BG_CARD)
            txt_lbl.pack(side="left")

            # Active indicator — 2 px bottom strip
            indicator = tk.Frame(btn_frame, bg=_BG_CARD, height=2)
            indicator.pack(fill="x", side="bottom")

            all_widgets = [btn_frame, inner, ico_lbl, txt_lbl]

            def _activate(bf=btn_frame, il=ico_lbl, tl=txt_lbl,
                          ind=indicator, iw=inner):
                for w in (bf, il, tl, iw):
                    w.config(bg=_BG_HL)
                il.config(fg=_ACCENT)
                tl.config(fg=_TEXT)
                ind.config(bg=_ACCENT)

            def _deactivate(bf=btn_frame, il=ico_lbl, tl=txt_lbl,
                            ind=indicator, iw=inner):
                for w in (bf, il, tl, iw):
                    w.config(bg=_BG_CARD)
                il.config(fg=_TEXT_MID)
                tl.config(fg=_TEXT_MID)
                ind.config(bg=_BG_CARD)

            def _hover_on(e, bf=btn_frame, il=ico_lbl, tl=txt_lbl, iw=inner,
                           i=idx):
                if self._active_tab_idx != i:
                    for w in (bf, il, tl, iw):
                        w.config(bg=_BG_HL)

            def _hover_off(e, bf=btn_frame, il=ico_lbl, tl=txt_lbl, iw=inner,
                            i=idx):
                if self._active_tab_idx != i:
                    for w in (bf, il, tl, iw):
                        w.config(bg=_BG_CARD)

            for w in all_widgets:
                w.bind("<Button-1>",  lambda e, i=idx: self._switch_tab(i))
                w.bind("<Enter>",     _hover_on)
                w.bind("<Leave>",     _hover_off)

            self._tab_activate.append(_activate)
            self._tab_deactivate.append(_deactivate)

        # Activate the first tab visually
        if self._tab_activate:
            self._tab_activate[0]()

        # Bottom divider
        tk.Frame(parent, bg=_BORDER_LT, height=1).pack(fill="x", side="top")

    def _switch_tab(self, idx: int) -> None:
        """Show the tab at *idx*, hide all others, and update button styles."""
        if idx == self._active_tab_idx and self._tab_panes:
            return

        # Deactivate old
        if 0 <= self._active_tab_idx < len(self._tab_deactivate):
            self._tab_deactivate[self._active_tab_idx]()
        if 0 <= self._active_tab_idx < len(self._tab_panes):
            self._tab_panes[self._active_tab_idx].pack_forget()

        # Activate new
        self._active_tab_idx = idx
        if 0 <= idx < len(self._tab_activate):
            self._tab_activate[idx]()
        if 0 <= idx < len(self._tab_panes):
            self._tab_panes[idx].pack(fill="both", expand=True)

        # Trigger any tab-specific refresh
        if idx < len(self._TAB_DEFS):
            name = self._TAB_DEFS[idx][2]
            if name == "HISTORY":
                self._refresh_history_tab()
            elif name == "TEMPLATES":
                self._refresh_templates_tab()

    # ── Status Bar ────────────────────────────────────────────────────────────
    def _build_statusbar(self) -> None:
        """Build the bottom status bar with bot state label and version info."""
        tk.Frame(self, bg=_BORDER, height=1).pack(fill="x", side="bottom")
        bar = tk.Frame(self, bg=_BG_CARD, height=24)
        bar.pack(fill="x", side="bottom")
        bar.pack_propagate(False)

        self._sb_cycle = tk.Label(
            bar, text="Cycle: —",
            font=_F_MONOS, fg=_TEXT_MID, bg=_BG_CARD)
        self._sb_cycle.pack(side="left", padx=14)

        tk.Frame(bar, bg=_BORDER_LT, width=1).pack(side="left", fill="y", pady=4)

        win_name = self._cfg.get("window_title", "—")
        self._sb_window = tk.Label(
            bar, text=f"Target: {win_name}",
            font=_F_MONOS, fg=_TEXT_MID, bg=_BG_CARD)
        self._sb_window.pack(side="left", padx=14)

        tk.Label(bar, text="pynput + OpenCV + Win32",
                 font=_F_MONOS, fg=_TEXT_DIM, bg=_BG_CARD
                 ).pack(side="right", padx=14)

    # ── Sidebar ───────────────────────────────────────────────────────────────
    def _build_sidebar(self, parent: tk.Frame) -> None:
        """Build the left sidebar containing bot controls, speed slider, and cycle counter."""
        side = tk.Frame(parent, bg=_BG_CARD, width=224)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)

        tk.Frame(parent, bg=_BORDER_LT, width=1).pack(side="left", fill="y")

        # Logo
        logo_wrap = tk.Frame(side, bg=_BG_CARD)
        logo_wrap.pack(pady=(20, 6))
        ico_p = _HERE / "chainex_icon.png"
        if ico_p.exists():
            try:
                img = Image.open(ico_p).resize((64, 64), Image.LANCZOS)
                self._sidebar_logo = ImageTk.PhotoImage(img)
                tk.Label(logo_wrap, image=self._sidebar_logo, bg=_BG_CARD).pack()
            except Exception:
                pass
        # Brand name under icon
        lname = tk.Frame(logo_wrap, bg=_BG_CARD)
        lname.pack(pady=(4, 0))
        tk.Label(lname, text="Chain", font=("Consolas", 11, "bold"),
                 fg=_TEXT, bg=_BG_CARD).pack(side="left")
        tk.Label(lname, text="EX", font=("Consolas", 11, "bold"),
                 fg=_ACCENT, bg=_BG_CARD).pack(side="left")

        # ── BOT CONTROL ──
        self._sidebar_section(side, "▶  BOT CONTROL")

        self._btn_main_start = HoverButton(
            side, text="▶  START BOT",
            command=self._start_bot,
            font=_F_HEAD, bg=_ACCENT_MU, fg=_ACCENT,
            hover_bg=_ACCENT, hover_fg="#000000", pady=11)
        self._btn_main_start.pack(fill="x", padx=14, pady=(3, 2))

        self._btn_main_stop = HoverButton(
            side, text="■  STOP BOT",
            command=self._stop_bot,
            state="disabled",
            font=_F_HEAD, bg=_BG_HL, fg=_DANGER,
            hover_bg=_DANGER, hover_fg="#000000", pady=11)
        self._btn_main_stop.pack(fill="x", padx=14, pady=(2, 3))

        self._btn_pause = HoverButton(
            side, text="⏸  PAUSE",
            command=self._toggle_pause,
            state="disabled",
            font=_F_HEAD, bg=_BG_HL, fg=_WARN,
            hover_bg=_WARN, hover_fg="#000000", pady=8)
        self._btn_pause.pack(fill="x", padx=14, pady=(0, 8))

        tk.Frame(side, bg=_BORDER_LT, height=1).pack(fill="x", padx=16, pady=2)

        # ── MACRO ──
        self._sidebar_section(side, "⚡  MACRO")

        HoverButton(side, text="⬤  RECORD NEW",
                    command=self._macro_record,
                    font=_F_HEAD, bg=_BG_HL, fg=_TEXT,
                    hover_bg=_DANGER, hover_fg="#ffffff", pady=10
                    ).pack(fill="x", padx=14, pady=3)

        self._btn_subloop = HoverButton(
            side, text="↺  SUB-LOOP: OFF",
            command=self._open_sub_loop_window,
            font=_F_HEAD, bg=_BG_HL, fg=_TEXT_MID,
            hover_bg=_BG_HL, hover_fg=_ACCENT, pady=10)
        self._btn_subloop.pack(fill="x", padx=14, pady=3)
        self._update_subloop_btn()

        tk.Frame(side, bg=_BORDER_LT, height=1).pack(fill="x", padx=16, pady=8)

        # ── PLAYBACK SPEED ──
        self._sidebar_section(side, "⏩  PLAYBACK SPEED")

        pb_var = tk.StringVar(
            value=str(self._get_cfg("macro.playback_speed") or "1.3"))
        self._cfg_vars["macro.playback_speed"] = pb_var

        speed_row = tk.Frame(side, bg=_BG_CARD)
        speed_row.pack(fill="x", padx=14, pady=(2, 0))

        self._pb_val_lbl = tk.Label(
            speed_row,
            text=f"{float(pb_var.get()):.1f}×",
            font=_F_MONOB, fg=_ACCENT, bg=_BG_CARD, width=5, anchor="e")
        self._pb_val_lbl.pack(side="right")

        def _speed_changed(val):
            self._pb_val_lbl.config(text=f"{float(val):.1f}×")
            self._handle_slider_update()

        tk.Scale(side,
                 from_=0.5, to=5.0, resolution=0.1,
                 variable=pb_var, orient="horizontal",
                 bg=_BG_CARD, fg=_ACCENT, troughcolor=_BG_HL,
                 highlightthickness=0, length=186,
                 showvalue=False, command=_speed_changed
                 ).pack(padx=14, pady=(0, 6))

        tk.Frame(side, bg=_BORDER_LT, height=1).pack(fill="x", padx=16, pady=8)
        self._sidebar_section(side, "⏰  SCHEDULE")
        self._build_scheduler_ui(side)

        # Cycle counter at bottom
        self._lbl_cycle = tk.Label(
            side, text="Cycle: — ms",
            font=_F_MONOS, fg=_TEXT_DIM, bg=_BG_CARD)
        self._lbl_cycle.pack(side="bottom", pady=(0, 10))

        # Hotkey reminder
        hk_frame = tk.Frame(side, bg=_BG_INSET)
        hk_frame.pack(side="bottom", fill="x", padx=10, pady=(0, 4))
        hk_cfg = self._cfg.get("hotkeys", {})
        for action, key in [
            ("Start",  hk_cfg.get("start",  "F6")),
            ("Stop",   hk_cfg.get("stop",   "F5")),
            ("Switch", hk_cfg.get("switch", "F8")),
            ("Pause",  hk_cfg.get("pause",  "F1")),
        ]:
            row = tk.Frame(hk_frame, bg=_BG_INSET)
            row.pack(fill="x", padx=8, pady=1)
            tk.Label(row, text=key.upper(), font=_F_LABEL, fg=_ACCENT,
                     bg=_BG_INSET, width=4, anchor="w").pack(side="left")
            tk.Label(row, text=action, font=_F_SMALL, fg=_TEXT_DIM,
                     bg=_BG_INSET, anchor="w").pack(side="left")

    def _sidebar_section(self, parent: tk.Frame, text: str) -> None:
        """Render a dimmed section-header label inside the sidebar."""
        tk.Label(parent, text=text,
                 font=_F_LABEL, fg=_TEXT_DIM, bg=_BG_CARD, anchor="w"
                 ).pack(fill="x", padx=18, pady=(10, 3))

    def _show_popup(self, top: tk.Toplevel, w: int, h: int) -> None:
        """Center a Toplevel on screen, raise it, and grab focus."""
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x  = max(0, (sw - w) // 2)
        y  = max(0, (sh - h) // 2)
        top.geometry(f"{w}x{h}+{x}+{y}")
        top.lift()
        top.focus_force()

    # ── Hotkeys ──────────────────────────────────────────────────────────────
    def _setup_hotkeys(self) -> None:
        """(Re)start the global pynput keyboard listener with current hotkey config."""
        if self._hk_listener:
            self._hk_listener.stop()
        self._hk_mods.clear()
        self._hk_listener = pynput_kb.Listener(
            on_press=self._on_hotkey_press,
            on_release=self._on_hotkey_release,
        )
        self._hk_listener.start()

    @staticmethod
    def _key_to_str(key) -> str:
        """Normalise a pynput key to a lowercase string like 'f6' or 'a'."""
        if hasattr(key, "char") and key.char:
            return key.char.lower().strip("'")
        name = str(key).split(".")[-1].strip("'").lower()
        return name

    def _on_hotkey_press(self, key) -> None:
        """pynput key-press callback: track modifiers and dispatch hotkey actions."""
        if key in _HK_MOD_KEYS:
            self._hk_mods.add(_HK_MOD_NAME[key])
            return
        if self._hotkey_capture_active:
            return
        try:
            k_str   = self._key_to_str(key)
            combo   = self._build_combo(k_str)
            hk        = self._cfg.get("hotkeys", {})
            start_hk  = str(hk.get("start",  "f6")).lower()
            stop_hk   = str(hk.get("stop",   "f5")).lower()
            switch_hk = str(hk.get("switch",  "f8")).lower()
            pause_hk  = str(hk.get("pause",   "f1")).lower()
            if   combo == start_hk:  self.after(0, self._start_bot)
            elif combo == stop_hk:   self.after(0, self._stop_bot)
            elif combo == switch_hk: self.after(0, self._trigger_secondary_switch)
            elif combo == pause_hk:  self.after(0, self._toggle_pause)
        except (ValueError, AttributeError):
            pass

    def _on_hotkey_release(self, key) -> None:
        """pynput key-release callback: clear modifier state."""
        if key in _HK_MOD_KEYS:
            self._hk_mods.discard(_HK_MOD_NAME[key])
            return
        if self._hotkey_capture_active:
            if key == pynput_kb.Key.esc:
                cb = self._hotkey_capture_cancel_cb
                self._hotkey_capture_active = False
                self._hotkey_capture_cb = None
                self._hotkey_capture_cancel_cb = None
                self._hk_mods.clear()
                if cb:
                    self.after(0, cb)
            elif self._hotkey_capture_cb:
                try:
                    k_str = self._key_to_str(key)
                    combo = self._build_combo(k_str)
                    cb = self._hotkey_capture_cb
                    self._hotkey_capture_active = False
                    self._hotkey_capture_cb = None
                    self._hotkey_capture_cancel_cb = None
                    self._hk_mods.clear()
                    self.after(0, lambda c=combo: cb(c))
                except (ValueError, AttributeError):
                    pass

    def _build_combo(self, key_str: str) -> str:
        """Build 'mod1+mod2+key' string from current modifier set + key name."""
        parts = sorted(self._hk_mods) + [key_str]
        return "+".join(parts)

    def _make_hotkey_capture(self, parent: tk.Frame, cfg_key: str) -> tk.Frame:
        """
        Returns a frame containing a label (current value) and a CHANGE button.
        Clicking CHANGE starts capture mode; the next non-modifier key release
        (with any held modifiers) becomes the new hotkey.
        """
        frame = tk.Frame(parent, bg=_BG_CARD)

        cur_val = str(self._cfg.get("hotkeys", {}).get(cfg_key.split(".")[-1], ""))
        disp_var = tk.StringVar(value=cur_val or "—")

        val_lbl = tk.Label(frame, textvariable=disp_var,
                           font=_F_MONOB, fg=_ACCENT, bg=_BG_CARD,
                           width=16, anchor="e")
        val_lbl.pack(side="left")

        status_lbl = tk.Label(frame, text="", font=_F_SMALL,
                              fg=_WARN, bg=_BG_CARD)
        status_lbl.pack(side="left", padx=(6, 0))

        def _cancel_capture() -> None:
            self._hotkey_capture_active    = False
            self._hotkey_capture_cb        = None
            self._hotkey_capture_cancel_cb = None
            self._hk_mods.clear()
            status_lbl.config(text="")
            cur = str(self._get_cfg(cfg_key))
            disp_var.set(cur if cur else "—")
            change_btn.config(state="normal", text="CHANGE")

        def _on_captured(combo: str) -> None:
            leaf = cfg_key.split(".")[-1]
            self._cfg.setdefault("hotkeys", {})[leaf] = combo
            self._save_cfg_silent()
            if cfg_key in self._cfg_vars:
                self._cfg_vars[cfg_key].set(combo)
            disp_var.set(combo)
            status_lbl.config(text="")
            change_btn.config(state="normal", text="CHANGE")

        def _start_capture() -> None:
            self._hk_mods.clear()
            self._hotkey_capture_active    = True
            self._hotkey_capture_cb        = _on_captured
            self._hotkey_capture_cancel_cb = _cancel_capture
            disp_var.set("…press combo…")
            status_lbl.config(text="[ESC = cancel]")
            change_btn.config(state="disabled", text="waiting")

        change_btn = HoverButton(frame, text="CHANGE",
                                 command=_start_capture,
                                 font=_F_SMALL,
                                 bg=_ACCENT_MU, fg=_ACCENT,
                                 hover_bg=_ACCENT, hover_fg="#000000",
                                 padx=10, pady=3)
        change_btn.pack(side="left", padx=(10, 0))

        return frame

    def _handle_slider_update(self, _=None) -> None:
        """Sync macro slider vars to cfg immediately; debounce the disk write."""
        for k, var in self._cfg_vars.items():
            if "macro." in k:
                val = var.get()
                d = self._cfg
                pts = k.split(".")
                for p in pts[:-1]:
                    d = d.setdefault(p, {})
                d[pts[-1]] = val
        # Cancel any pending write and reschedule — only flush after 400ms of idle
        if self._slider_save_id is not None:
            self.after_cancel(self._slider_save_id)
        self._slider_save_id = self.after(400, self._flush_slider_save)

    def _flush_slider_save(self) -> None:
        """Debounced handler: write config to disk once slider is idle."""
        self._slider_save_id = None
        self._save_cfg_silent()

    # ── Dashboard ─────────────────────────────────────────────────────────────
    def _build_dashboard(self) -> None:
        """Build the Dashboard tab: hero stats, live feed canvas, and log panel."""
        # ── Hero banner ───────────────────────────────────────────────────────
        hero = tk.Frame(self._tab_dash, bg=_BG_CARD,
                        highlightthickness=1, highlightbackground=_BORDER_LT)
        hero.pack(fill="x", padx=14, pady=(14, 8))

        # Full brand logo (chain + "ChainEX" text) rendered as image
        logo_p = _HERE / "chainex_logo.png"
        if logo_p.exists():
            try:
                logo_img = Image.open(logo_p)
                # Scale to a clean height that fits the hero banner
                target_h = 70
                scale    = target_h / logo_img.height
                target_w = int(logo_img.width * scale)
                logo_img = logo_img.resize((target_w, target_h), Image.LANCZOS)
                self._dash_logo = ImageTk.PhotoImage(logo_img)
                tk.Label(hero, image=self._dash_logo,
                         bg=_BG_CARD).pack(side="left", padx=(18, 18), pady=18)
            except Exception:
                pass
        else:
            # Fallback text if image not found
            txt_col = tk.Frame(hero, bg=_BG_CARD)
            txt_col.pack(side="left", fill="y", pady=14, padx=(18, 14))
            tk.Label(txt_col, text="Chain", font=("Consolas", 24, "bold"),
                     fg=_TEXT, bg=_BG_CARD).pack(side="left")
            tk.Label(txt_col, text="EX", font=("Consolas", 24, "bold"),
                     fg=_ACCENT, bg=_BG_CARD).pack(side="left")

        txt_col = tk.Frame(hero, bg=_BG_CARD)
        txt_col.pack(side="left", fill="y", pady=14)

        tk.Label(txt_col, text="Macro Automation Platform",
                 font=_F_BODY,
                 fg=_TEXT_MID, bg=_BG_CARD, anchor="w").pack(anchor="w")
        tk.Label(txt_col, text="Record  ·  Automate  ·  Chain",
                 font=_F_SMALL,
                 fg=_TEXT_DIM, bg=_BG_CARD, anchor="w").pack(anchor="w", pady=(4, 0))

        tk.Label(hero, text=_VERSION,
                 font=("Consolas", 11, "bold"),
                 fg=_BORDER_LT, bg=_BG_CARD
                 ).pack(side="right", padx=20)

        # Stats strip — row 1: live runtime counters
        stats_row = tk.Frame(self._tab_dash, bg=_BG)
        stats_row.pack(fill="x", padx=14, pady=(0, 4))

        self._stat_card(stats_row, "LOOPS", "0", "completed", tag="loops").pack(
            side="left", expand=True, fill="x", padx=(0, 4))
        self._stat_card(stats_row, "AVG LOOP", "—", "sec", tag="avg_loop").pack(
            side="left", expand=True, fill="x", padx=4)
        self._stat_card(stats_row, "LAST LOOP", "—", "sec", tag="last_loop").pack(
            side="left", expand=True, fill="x", padx=4)
        self._stat_card(stats_row, "CYCLE TIME", "—", "ms", tag="cycle_ms").pack(
            side="left", expand=True, fill="x", padx=4) # session timer card
        self._stat_card(stats_row, "SESSION", "00:00", "", tag="session").pack(
            side="left", expand=True, fill="x", padx=(4, 0))

        # Stats strip — row 2: config info
        stats_row2 = tk.Frame(self._tab_dash, bg=_BG)
        stats_row2.pack(fill="x", padx=14, pady=(0, 8))

        tpl_n = str(len(self._collect_template_stems()))
        self._stat_card(stats_row2, "TEMPLATES", tpl_n, "loaded").pack(
            side="left", expand=True, fill="x", padx=(0, 4))
        win = self._cfg.get("window_title", "—")
        self._stat_card(stats_row2, "TARGET WINDOW", win, "").pack(
            side="left", expand=True, fill="x", padx=(4, 0))

        # ── Remote dashboard URL strip ────────────────────────────────────────
        remote_strip = tk.Frame(
            self._tab_dash, bg=_BG_CARD,
            highlightthickness=1, highlightbackground=_BORDER_LT)
        remote_strip.pack(fill="x", padx=14, pady=(0, 8))
        tk.Label(remote_strip, text="REMOTE",
                 font=_F_LABEL, fg=_TEXT_DIM, bg=_BG_CARD
                 ).pack(side="left", padx=(12, 6), pady=8)
        self._lbl_remote_url = tk.Label(
            remote_strip,
            text="📡  Starting…" if self._cfg.get("remote", {}).get("enabled", True)
                 else "📡  Remote dashboard disabled",
            font=_F_MONO, fg=_ACCENT, bg=_BG_CARD, anchor="w",
            cursor="hand2")
        self._lbl_remote_url.pack(side="left", fill="x", expand=True, pady=8)
        Tooltip(self._lbl_remote_url,
                "Open this URL on your phone (same Wi-Fi) to control the bot remotely")

        # Feed + Log side by side
        panes = tk.Frame(self._tab_dash, bg=_BG)
        panes.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        panes.columnconfigure(0, weight=56)
        panes.columnconfigure(1, weight=44)
        panes.rowconfigure(0, weight=1)

        # Feed panel
        feed_wrap = tk.Frame(panes, bg=_BORDER_LT, padx=1, pady=1)
        feed_wrap.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        feed_hdr = tk.Frame(feed_wrap, bg=_BG_CARD, pady=7)
        feed_hdr.pack(fill="x")
        tk.Label(feed_hdr, text="SENSOR FEED",
                 font=_F_LABEL, fg=_ACCENT, bg=_BG_CARD
                 ).pack(side="left", padx=12)
        self._feed_canvas = tk.Canvas(feed_wrap, bg=_BG_INSET, highlightthickness=0)
        self._feed_canvas.pack(fill="both", expand=True)
        self.after(150, self._draw_feed_placeholder)

        # Log panel
        log_wrap = tk.Frame(panes, bg=_BORDER_LT, padx=1, pady=1)
        log_wrap.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        log_hdr = tk.Frame(log_wrap, bg=_BG_CARD, pady=7)
        log_hdr.pack(fill="x")
        tk.Label(log_hdr, text="EVENT LOG",
                 font=_F_LABEL, fg=_ACCENT, bg=_BG_CARD
                 ).pack(side="left", padx=12)
        HoverButton(log_hdr, text="CLR",
                    command=lambda: self._log_box.delete("1.0", "end"),
                    font=_F_SMALL, bg=_BG_CARD, fg=_TEXT_MID,
                    hover_bg=_BG_HL, hover_fg=_TEXT
                    ).pack(side="right", padx=8)
        # Log level filter buttons
        for lvl, color in (("ERR", _DANGER), ("WRN", _WARN), ("INF", _ACCENT), ("DBG", _TEXT_DIM)):
            HoverButton(log_hdr, text=lvl,
                        command=lambda l=lvl: self._set_log_filter(l),
                        font=_F_SMALL, bg=_BG_CARD, fg=color,
                        hover_bg=_BG_HL, hover_fg=color
                        ).pack(side="right", padx=2)
        HoverButton(log_hdr, text="ALL",
                    command=lambda: self._set_log_filter("ALL"),
                    font=_F_SMALL, bg=_BG_CARD, fg=_TEXT_MID,
                    hover_bg=_BG_HL, hover_fg=_TEXT
                    ).pack(side="right", padx=2)
        self._log_box = scrolledtext.ScrolledText(
            log_wrap,
            bg=_BG_INSET, fg=_ACCENT,
            font=_F_MONO,
            borderwidth=0, highlightthickness=0,
            wrap="word",
            insertbackground=_ACCENT)
        self._log_box.pack(fill="both", expand=True)
        self._log_box.tag_config("DEBUG",   foreground=_LOG_DEBUG)
        self._log_box.tag_config("INFO",    foreground=_LOG_INFO)
        self._log_box.tag_config("WARNING", foreground=_LOG_WARNING)
        self._log_box.tag_config("ERROR",   foreground=_LOG_ERROR)

    def _draw_feed_placeholder(self) -> None:
        if self._feed_canvas is None:
            return
        self._feed_canvas.delete("placeholder")
        w = self._feed_canvas.winfo_width()
        h = self._feed_canvas.winfo_height()
        if w < 20:
            self.after(200, self._draw_feed_placeholder)
            return
        self._feed_canvas.create_text(
            w // 2, h // 2,
            text="NO SIGNAL",
            fill=_TEXT_DIM,
            font=("Consolas", 16, "bold"),
            tags="placeholder")

    def _stat_card(self, parent: tk.Frame, label: str,
                   value: str, unit: str, *, tag: str = "") -> tk.Frame:
        card = tk.Frame(parent, bg=_BG_CARD,
                        highlightthickness=1, highlightbackground=_BORDER_LT)
        tk.Label(card, text=label, font=_F_LABEL, fg=_TEXT_DIM,
                 bg=_BG_CARD, anchor="w"
                 ).pack(fill="x", padx=12, pady=(8, 0))
        row = tk.Frame(card, bg=_BG_CARD)
        row.pack(fill="x", padx=12, pady=(2, 10))
        val_lbl = tk.Label(row, text=value,
                           font=("Consolas", 18, "bold"), fg=_ACCENT, bg=_BG_CARD)
        val_lbl.pack(side="left")
        if tag:
            self._stat_vals[tag] = val_lbl
        if unit:
            tk.Label(row, text=f"  {unit}",
                     font=_F_BODY, fg=_TEXT_MID, bg=_BG_CARD
                     ).pack(side="left", pady=(5, 0))
        return card

    # ── Macro Editor ──────────────────────────────────────────────────────────
    def _build_macro_editor(self) -> None:
        """Build the Macro tab: toolbar, sequence list, sub-loop and trigger controls."""
        hdr = tk.Frame(self._tab_macro, bg=_BG)
        hdr.pack(fill="x", padx=14, pady=(14, 6))
        tk.Label(hdr, text="MACRO FLOW & LOGIC",
                 font=_F_TITLE, fg=_ACCENT, bg=_BG).pack(side="left")
        HoverButton(hdr, text="📤  EXPORT", command=self._macro_export,
                    font=_F_SMALL, bg=_BG_CARD, fg=_TEXT_MID,
                    hover_bg=_BG_HL, hover_fg=_TEXT,
                    pady=5, padx=10).pack(side="right", padx=(4, 0))
        self._btn_macro_import = HoverButton(
                    hdr, text="📥  IMPORT", command=self._macro_import,
                    font=_F_SMALL, bg=_BG_CARD, fg=_TEXT_MID,
                    hover_bg=_BG_HL, hover_fg=_TEXT,
                    pady=5, padx=10)
        self._btn_macro_import.pack(side="right", padx=4)
        self._btn_macro_clear = HoverButton(
                    hdr, text="🗑  CLEAR", command=self._macro_clear,
                    font=_F_SMALL, bg=_BG_CARD, fg=_DANGER,
                    hover_bg=_DANGER, hover_fg="#000",
                    pady=5, padx=10)
        self._btn_macro_clear.pack(side="right", padx=4)

        # Controls card
        ctrl_wrap = tk.Frame(self._tab_macro, bg=_BORDER_LT, padx=1, pady=1)
        ctrl_wrap.pack(fill="x", padx=14, pady=(0, 6))
        ctrl = tk.Frame(ctrl_wrap, bg=_BG_CARD, pady=8)
        ctrl.pack(fill="x")

        tk.Label(ctrl, text="CLICK DELAY (ms):",
                 font=_F_LABEL, fg=_TEXT_MID, bg=_BG_CARD
                 ).grid(row=0, column=0, padx=14, sticky="w")
        speed_var = tk.StringVar(value=str(self._get_cfg("macro.click_delay_ms") or "800"))
        self._cfg_vars["macro.click_delay_ms"] = speed_var
        tk.Scale(ctrl, from_=50, to=3000, resolution=50,
                 orient="horizontal", variable=speed_var,
                 bg=_BG_CARD, fg=_TEXT, troughcolor=_BG_HL,
                 highlightthickness=0, length=180, showvalue=True,
                 command=self._handle_slider_update
                 ).grid(row=0, column=1, padx=6)

        tk.Label(ctrl, text="PLAYBACK SPEED (×):",
                 font=_F_LABEL, fg=_TEXT_MID, bg=_BG_CARD
                 ).grid(row=0, column=2, padx=(20, 6), sticky="w")
        pb_var = self._cfg_vars["macro.playback_speed"]
        tk.Scale(ctrl, from_=0.5, to=5.0, resolution=0.1,
                 orient="horizontal", variable=pb_var,
                 bg=_BG_CARD, fg=_TEXT, troughcolor=_BG_HL,
                 highlightthickness=0, length=140, showvalue=True,
                 command=self._handle_slider_update
                 ).grid(row=0, column=3, padx=6)

        # Toolbar: add-step buttons
        toolbar = tk.Frame(self._tab_macro, bg=_BG)
        toolbar.pack(fill="x", padx=14, pady=(0, 6))

        _toolbar_tips = {
            "+ POS":       "Add a click at fixed screen coordinates.\nUse 📷 to capture by clicking.",
            "+ WAIT":      "Add a timed delay between steps.",
            "+ KEY":       "Add a keyboard keypress (e.g. SPACE, ESC).",
            "+ TEMPLATE":  "Add a step that finds an image on screen\nand clicks it when visible.",
            "⚡ TRIGGERS": "Configure global interrupt conditions\nthat can jump to any step mid-loop.",
            "● RECORD":    "Record mouse clicks live from the game window\nand convert them into POS steps.",
        }
        for txt, tb_bg, accent, fg, cmd in [
            ("+ POS",       _TB_POS,  _ORANGE, _ORANGE, self._add_pos_step),
            ("+ WAIT",      _TB_WAIT, _YELLOW, _YELLOW, self._add_wait_step),
            ("+ KEY",       _TB_KEY,  _PURPLE, _PURPLE, self._add_key_step),
            ("+ TEMPLATE",  _TB_TPL,  _ACCENT, _ACCENT, self._add_tpl_step),
            ("⚡ TRIGGERS", _TB_TRIG, _WARN,   _WARN,   self._open_triggers_window),
            ("● RECORD",    _TB_REC,  _DANGER, _DANGER, self._macro_record),
        ]:
            btn = HoverButton(toolbar, text=txt, command=cmd,
                              font=_F_SMALL, bg=tb_bg, fg=fg,
                              hover_bg=accent, hover_fg="#000",
                              pady=7, padx=12)
            btn.pack(side="left", padx=3)
            Tooltip(btn, _toolbar_tips[txt])

        # Undo / Redo — packed to the right of the toolbar
        self._btn_redo = HoverButton(
            toolbar, text="↪ REDO", command=self._redo,
            font=_F_SMALL, bg=_BG_HL, fg=_TEXT_DIM,
            hover_bg=_BG_CARD, hover_fg=_ACCENT,
            pady=7, padx=10, state="disabled")
        self._btn_redo.pack(side="right", padx=(0, 3))

        self._btn_undo = HoverButton(
            toolbar, text="↩ UNDO", command=self._undo,
            font=_F_SMALL, bg=_BG_HL, fg=_TEXT_DIM,
            hover_bg=_BG_CARD, hover_fg=_ACCENT,
            pady=7, padx=10, state="disabled")
        self._btn_undo.pack(side="right", padx=3)

        # ── Search / filter bar ───────────────────────────────────────────────
        srch_row = tk.Frame(self._tab_macro, bg=_BG_CARD)
        srch_row.pack(fill="x", padx=14, pady=(0, 4))
        tk.Label(srch_row, text="🔍", bg=_BG_CARD, fg=_TEXT_MID,
                 font=_F_BODY).pack(side="left", padx=(8, 4), pady=5)
        self._macro_search_var = tk.StringVar()
        srch_e = tk.Entry(srch_row, textvariable=self._macro_search_var,
                          bg=_ENTRY_BG, fg=_TEXT, insertbackground=_TEXT,
                          relief="flat", highlightthickness=1,
                          highlightbackground=_ENTRY_BD, highlightcolor=_ENTRY_FC,
                          font=_F_BODY)
        srch_e.pack(side="left", ipady=4, fill="x", expand=True, pady=5)
        Tooltip(srch_e, "Filter steps by template name, command, or label")
        HoverButton(srch_row, text="✕",
                    command=lambda: self._macro_search_var.set(""),
                    bg=_BG_CARD, fg=_TEXT_DIM, font=_F_SMALL,
                    hover_bg=_BG_HL, hover_fg=_DANGER,
                    padx=6, pady=3).pack(side="left", padx=(4, 4), pady=5)
        self._lbl_filter_count = tk.Label(
            srch_row, text="", font=_F_SMALL, fg=_TEXT_DIM, bg=_BG_CARD)
        self._lbl_filter_count.pack(side="left", padx=(0, 8), pady=5)
        self._macro_search_var.trace_add("write", lambda *_: self._refresh_macro_list())

        # Column header strip
        col_hdr = tk.Frame(self._tab_macro, bg=_BORDER_LT, pady=6)
        col_hdr.pack(fill="x", padx=14)
        tk.Label(col_hdr, text=" #", width=3, bg=_BORDER_LT, fg=_TEXT_MID,
                 font=_F_LABEL, anchor="e").pack(side="left", padx=(10, 6))
        tk.Label(col_hdr, text="TYPE",
                 bg=_BORDER_LT, fg=_TEXT_MID, font=_F_LABEL, width=8
                 ).pack(side="left", padx=(0, 8))
        tk.Label(col_hdr, text="NAME  ·  VALUE / PARAMETERS",
                 bg=_BORDER_LT, fg=_TEXT_MID, font=_F_LABEL).pack(side="left")
        tk.Label(col_hdr, text="⠿ DRAG  ·  ⧉  ✕",
                 bg=_BORDER_LT, fg=_TEXT_MID, font=_F_LABEL
                 ).pack(side="right", padx=14)

        # Scrollable inline step rows
        outer = tk.Frame(self._tab_macro, bg=_BORDER_LT, padx=1, pady=1)
        outer.pack(fill="both", expand=True, padx=14, pady=(0, 14))

        canvas = tk.Canvas(outer, bg=_BG_INSET, highlightthickness=0)
        sb     = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        self._step_rows_frame = tk.Frame(canvas, bg=_BG_INSET)
        self._macro_canvas    = canvas
        _win = canvas.create_window((0, 0), window=self._step_rows_frame, anchor="nw")

        self._step_rows_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfig(_win, width=e.width))

        def _on_scroll(e):
            canvas.yview_scroll(-1 * (e.delta // 120), "units")

        canvas.bind("<MouseWheel>", _on_scroll)
        self._scroll_handler = _on_scroll  # stored so rows can bind to it

        # Estimated loop time + step count footer
        footer = tk.Frame(self._tab_macro, bg=_BG_CARD)
        footer.pack(fill="x", padx=14, pady=(0, 6))
        self._lbl_est_time = tk.Label(
            footer, text="Est. loop: —",
            font=_F_MONOS, fg=_TEXT_DIM, bg=_BG_CARD, anchor="w")
        self._lbl_est_time.pack(side="left", padx=10, pady=4)
        self._lbl_step_count = tk.Label(
            footer, text="0 steps",
            font=_F_MONOS, fg=_TEXT_DIM, bg=_BG_CARD, anchor="e")
        self._lbl_step_count.pack(side="right", padx=10, pady=4)

    # ── Profiles ──────────────────────────────────────────────────────────────
    def _build_profiles_tab(self) -> None:
        """Build the Profiles tab: profile list, active indicator, and LOAD/SAVE/SAVE AS/DELETE buttons."""
        note_bar = tk.Frame(self._tab_profiles, bg=_ACCENT_MU)
        note_bar.pack(fill="x", padx=14, pady=(14, 0))
        tk.Label(note_bar,
                 text="  ℹ  Load a profile to apply it immediately — no restart needed.  ",
                 font=_F_SMALL, fg=_WARN, bg=_ACCENT_MU, pady=6
                 ).pack(side="left")

        # Active profile indicator
        active_bar = tk.Frame(self._tab_profiles, bg=_BG_CARD)
        active_bar.pack(fill="x", padx=14, pady=(6, 0))
        tk.Label(active_bar, text="Active:", font=_F_SMALL,
                 fg=_TEXT_DIM, bg=_BG_CARD).pack(side="left", padx=(8, 4), pady=4)
        self._lbl_active_prof = tk.Label(
            active_bar, text="— none —",
            font=("Segoe UI", 9, "bold"), fg=_TEXT_MID, bg=_BG_CARD)
        self._lbl_active_prof.pack(side="left", pady=4)

        body = tk.Frame(self._tab_profiles, bg=_BG)
        body.pack(fill="both", expand=True, padx=14, pady=14)

        list_wrap = tk.Frame(body, bg=_BORDER_LT, padx=1, pady=1)
        list_wrap.pack(side="left", fill="both", expand=True)
        self._profile_list = tk.Listbox(
            list_wrap, bg=_BG_INSET, fg=_TEXT, font=_F_BODY,
            selectbackground=_ACCENT_MU, selectforeground=_ACCENT,
            activestyle="none", borderwidth=0, highlightthickness=0)
        self._profile_list.pack(fill="both", expand=True)

        btn_col = tk.Frame(body, bg=_BG)
        btn_col.pack(side="right", fill="y", padx=(12, 0))

        # LOAD
        HoverButton(btn_col, text="LOAD", command=self._profile_load,
                    font=_F_HEAD, bg=_ACCENT_MU, fg=_ACCENT,
                    hover_bg=_ACCENT, hover_fg="#000000",
                    width=12, pady=11).pack(pady=4, fill="x")

        # SAVE (overwrite the active profile) — disabled until a profile is loaded
        self._btn_profile_save = HoverButton(
            btn_col, text="SAVE", command=self._profile_save,
            font=_F_HEAD, bg=_BG_HL, fg=_TEXT_DIM,
            hover_bg=_SUCCESS, hover_fg="#000000",
            width=12, pady=11, state="disabled")
        self._btn_profile_save.pack(pady=4, fill="x")

        # SAVE AS (new name)
        HoverButton(btn_col, text="SAVE AS", command=self._profile_save_as,
                    font=_F_HEAD, bg=_BG_HL, fg=_TEXT,
                    hover_bg=_BG_CARD, hover_fg=_ACCENT,
                    width=12, pady=11).pack(pady=4, fill="x")

        # DUPLICATE
        HoverButton(btn_col, text="DUPLICATE", command=self._profile_duplicate,
                    font=_F_HEAD, bg=_BG_HL, fg=_TEXT,
                    hover_bg=_BG_CARD, hover_fg=_ACCENT,
                    width=12, pady=11).pack(pady=4, fill="x")

        # DELETE
        HoverButton(btn_col, text="DELETE", command=self._profile_del,
                    font=_F_HEAD, bg=_BG_HL, fg=_DANGER,
                    hover_bg=_DANGER, hover_fg="#000000",
                    width=12, pady=11).pack(pady=4, fill="x")

    # ── Settings ──────────────────────────────────────────────────────────────
    def _build_settings(self) -> None:
        outer = tk.Frame(self._tab_settings, bg=_BG)
        outer.pack(fill="both", expand=True)

        canvas = tk.Canvas(outer, bg=_BG, highlightthickness=0)
        sb     = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        body = tk.Frame(canvas, bg=_BG)
        _win = canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>",
                  lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfig(_win, width=e.width))
        # Note: MouseWheel is not bound here — _bind_scroll_tree at the end
        # of this method covers the canvas and every child widget recursively.

        def _section(title: str, info: str = "") -> None:
            hdr = tk.Frame(body, bg=_BORDER_LT, pady=6)
            hdr.pack(fill="x", padx=20, pady=(18, 4))
            tk.Label(hdr, text=f"  {title}", font=_F_HEAD,
                     fg=_ACCENT, bg=_BORDER_LT).pack(side="left")
            if info:
                tk.Label(hdr, text=info, font=_F_SMALL,
                         fg=_TEXT_DIM, bg=_BORDER_LT).pack(side="right", padx=10)

        # field type constants
        TEXT = "text"
        BOOL = "bool"
        SLIDER = "slider"

        def _row(cfg_key: str, label: str, kind: str,
                 hint: str = "", slider_from: float = 0,
                 slider_to: float = 1, slider_res: float = 0.01,
                 tooltip: str = "") -> None:
            wrap = tk.Frame(body, bg=_BORDER, padx=1, pady=1)
            wrap.pack(fill="x", padx=20, pady=3)
            row = tk.Frame(wrap, bg=_BG_CARD, pady=10)
            row.pack(fill="x")

            lbl_col = tk.Frame(row, bg=_BG_CARD)
            lbl_col.pack(side="left", padx=14)
            lbl_w = tk.Label(lbl_col, text=label, font=_F_BODY,
                             fg=_TEXT, bg=_BG_CARD, anchor="w")
            lbl_w.pack(anchor="w")
            tip_text = tooltip or hint
            if tip_text:
                Tooltip(lbl_w, tip_text)
            if hint:
                tk.Label(lbl_col, text=hint, font=_F_SMALL,
                         fg=_TEXT_DIM, bg=_BG_CARD, anchor="w").pack(anchor="w")

            cur = self._get_cfg(cfg_key)

            if kind == BOOL:
                v = tk.BooleanVar(value=bool(cur))
                self._cfg_vars[cfg_key] = v
                tk.Checkbutton(row, variable=v, bg=_BG_CARD,
                               selectcolor=_ACCENT_MU,
                               activebackground=_BG_CARD,
                               cursor="hand2"
                               ).pack(side="right", padx=14)

            elif kind == SLIDER:
                v = tk.DoubleVar(value=float(cur) if cur != "" else slider_from)
                self._cfg_vars[cfg_key] = v
                val_lbl = tk.Label(row, text=f"{v.get():.2f}",
                                   font=_F_MONOB, fg=_ACCENT,
                                   bg=_BG_CARD, width=5)
                val_lbl.pack(side="right", padx=(0, 14))
                def _on_slide(val, lbl=val_lbl, res=slider_res):
                    lbl.config(text=f"{float(val):.{max(0,len(str(res).rstrip('0').split('.')[-1]))}f}")
                tk.Scale(row, from_=slider_from, to=slider_to,
                         resolution=slider_res, variable=v,
                         orient="horizontal", showvalue=False,
                         bg=_BG_CARD, fg=_ACCENT, troughcolor=_BG_HL,
                         highlightthickness=0, length=180,
                         command=_on_slide
                         ).pack(side="right", padx=(0, 6))

            else:  # TEXT
                v = tk.StringVar(value=str(cur) if cur != "" else "")
                self._cfg_vars[cfg_key] = v
                tk.Entry(row, textvariable=v, font=_F_MONO,
                         bg=_ENTRY_BG, fg=_ACCENT,
                         insertbackground=_ACCENT,
                         highlightthickness=1,
                         highlightbackground=_ENTRY_BD,
                         highlightcolor=_ACCENT,
                         relief="flat"
                         ).pack(side="right", fill="x", expand=True, padx=14)

        # ── Window ────────────────────────────────────────────────────────────
        _section("WINDOW", "controls which app the bot attaches to")
        _row("window_title", "Target Window Title", TEXT,
             "Substring of the game window's title bar text")
        # Window picker button
        _pick_wrap = tk.Frame(body, bg=_BG)
        _pick_wrap.pack(fill="x", padx=20, pady=(0, 6))
        HoverButton(_pick_wrap, text="⊞  BROWSE WINDOWS",
                    command=lambda: self._pick_window_title(self._cfg_vars["window_title"]),
                    bg=_BG_CARD, fg=_TEXT_MID,
                    hover_bg=_BG_HL, hover_fg=_TEXT,
                    font=_F_SMALL, pady=5
                    ).pack(side="left")

        # ── Hotkeys ───────────────────────────────────────────────────────────
        _section("HOTKEYS", "global — work even when the panel is not focused")

        def _hk_row(cfg_key: str, label: str, hint: str) -> None:
            wrap = tk.Frame(body, bg=_BORDER, padx=1, pady=1)
            wrap.pack(fill="x", padx=20, pady=3)
            row = tk.Frame(wrap, bg=_BG_CARD, pady=10)
            row.pack(fill="x")
            lbl_col = tk.Frame(row, bg=_BG_CARD)
            lbl_col.pack(side="left", padx=14)
            tk.Label(lbl_col, text=label, font=_F_BODY,
                     fg=_TEXT, bg=_BG_CARD, anchor="w").pack(anchor="w")
            tk.Label(lbl_col, text=hint, font=_F_SMALL,
                     fg=_TEXT_DIM, bg=_BG_CARD, anchor="w").pack(anchor="w")
            self._make_hotkey_capture(row, cfg_key).pack(side="right", padx=14)

        _hk_row("hotkeys.start", "Start Bot",
                "Click CHANGE, then press any key or combo (e.g. Ctrl+F6)")
        _hk_row("hotkeys.stop",  "Stop Bot",
                "Click CHANGE, then press any key or combo (e.g. F5)")
        _hk_row("hotkeys.pause", "Pause / Resume",
                "Toggle pause mid-run without losing your place  (e.g. F7)")

        # ── Secondary Macro ───────────────────────────────────────────────────
        _section("SECONDARY MACRO",
                 "press hotkey while running → switch after current loop finishes")

        # Hotkey row (reuses the capture widget)
        _hk_row("hotkeys.switch", "Switch Hotkey",
                "Click CHANGE, press a key or combo (e.g. F8)")
        _row("secondary_macro.run_once", "Run Once then Return", BOOL,
             "Secondary macro runs a single pass, then the primary macro resumes")

        # Profile picker row
        prof_wrap = tk.Frame(body, bg=_BORDER, padx=1, pady=1)
        prof_wrap.pack(fill="x", padx=20, pady=3)
        prof_row = tk.Frame(prof_wrap, bg=_BG_CARD, pady=10)
        prof_row.pack(fill="x")

        prof_lbl_col = tk.Frame(prof_row, bg=_BG_CARD)
        prof_lbl_col.pack(side="left", padx=14)
        tk.Label(prof_lbl_col, text="Secondary Profile", font=_F_BODY,
                 fg=_TEXT, bg=_BG_CARD, anchor="w").pack(anchor="w")
        tk.Label(prof_lbl_col, text="Profile from the Profiles tab to run after the switch",
                 font=_F_SMALL, fg=_TEXT_DIM, bg=_BG_CARD, anchor="w").pack(anchor="w")

        prof_right = tk.Frame(prof_row, bg=_BG_CARD)
        prof_right.pack(side="right", padx=14)

        profiles_list = list_profiles(str(_HERE))
        cur_sec_prof  = str(self._cfg.get("secondary_macro", {}).get("profile", ""))
        sec_prof_var  = tk.StringVar(
            value=cur_sec_prof if cur_sec_prof in profiles_list else
                  (profiles_list[0] if profiles_list else "— no profiles —"))
        self._sec_prof_var = sec_prof_var   # keep ref for _refresh_cfg_panel

        def _on_sec_prof_change(*_):
            chosen = sec_prof_var.get()
            self._cfg.setdefault("secondary_macro", {})["profile"] = chosen
            self._save_cfg_silent()

        sec_prof_var.trace_add("write", _on_sec_prof_change)

        options = profiles_list if profiles_list else ["— no profiles —"]
        sec_menu = tk.OptionMenu(prof_right, sec_prof_var, *options)
        sec_menu.config(bg=_ENTRY_BG, fg=_ACCENT, activebackground=_BG_HL,
                        activeforeground=_TEXT, relief="flat",
                        font=_F_MONO, bd=0, highlightthickness=1,
                        highlightbackground=_ENTRY_BD, highlightcolor=_ACCENT,
                        indicatoron=True, width=22)
        sec_menu["menu"].config(bg=_BG_CARD, fg=_TEXT, activebackground=_ACCENT_MU,
                                activeforeground=_ACCENT, bd=0)
        sec_menu.pack(side="left")

        def _refresh_sec_menu() -> None:
            new_list = list_profiles(str(_HERE))
            menu = sec_menu["menu"]
            menu.delete(0, "end")
            for p in (new_list if new_list else ["— no profiles —"]):
                menu.add_command(label=p, command=lambda v=p: sec_prof_var.set(v))

        HoverButton(prof_right, text="↺",
                    command=_refresh_sec_menu,
                    font=_F_SMALL, bg=_ACCENT_MU, fg=_ACCENT,
                    hover_bg=_ACCENT, hover_fg="#000000",
                    padx=6, pady=3
                    ).pack(side="left", padx=(6, 0))

        # ── Click Behaviour ───────────────────────────────────────────────────
        _section("CLICK BEHAVIOUR")
        _row("input.force_global_click", "Force Global Click Mode", BOOL,
             "Override every step to use the physical mouse (SetCursorPos + mouse_event)",
             tooltip="When ON, every click uses absolute screen coords via the Windows API.\n"
                     "Useful if local PostMessage clicks don't register in the game.")
        _row("input.focus_before_actions", "Focus Window Before Each Action", BOOL,
             "Bring the game window to the foreground before every click / key press",
             tooltip="Ensures the game window has focus before sending any input.\n"
                     "Turn OFF if you want the bot to run in the background.")
        _row("input.random_click_offset_px", "Random Click Offset (px)", TEXT,
             "Adds ±N px jitter to template clicks to humanise timing  (0 = off)",
             tooltip="Enter a pixel radius, e.g. 5 to randomise clicks by ±5 px.\n"
                     "Helps avoid detection by anti-bot systems. Set 0 to disable.")

        # ── Vision ────────────────────────────────────────────────────────────
        _section("VISION", "template image matching")
        _row("vision.match_threshold", "Match Confidence Threshold", SLIDER,
             "0.0 = match anything · 1.0 = perfect match only  (default 0.5)",
             slider_from=0.1, slider_to=1.0, slider_res=0.01,
             tooltip="Controls how closely a screen region must match a template image.\n"
                     "Lower = more permissive (more false positives).\n"
                     "Higher = stricter (may miss a match if lighting varies).\n"
                     "Recommended: 0.50–0.75.")

        # ── Timing ────────────────────────────────────────────────────────────
        _section("TIMING")
        _row("macro.wait_timeout_s", "Template Wait Timeout (sec)", TEXT,
             "Skip a template step if it isn't found within this many seconds",
             tooltip="If the bot looks for a template image and doesn't find it within\n"
                     "this many seconds, it moves on to the next step.\n"
                     "Example: 5  →  give up after 5 seconds.")
        _row("timing.max_runtime_minutes", "Max Runtime (minutes)", TEXT,
             "Auto-stop the bot after N minutes  (0 = run forever)",
             tooltip="Safety limit — the bot stops automatically after this many minutes.\n"
                     "Set to 0 to run indefinitely.")
        _row("timing.reconnect_timeout_s", "Reconnect Timeout (sec)", TEXT,
             "Seconds to wait for the game window to reappear after it closes  (0 = disabled)",
             tooltip="If the game window closes unexpectedly, the bot waits up to this\n"
                     "many seconds for it to reappear before giving up.\n"
                     "Set to 0 to disable reconnect attempts.")

        # ── Remote Dashboard ──────────────────────────────────────────────────
        _section("REMOTE DASHBOARD",
                 "control the bot from your phone on the same Wi-Fi")
        _row("remote.enabled", "Enable Remote Dashboard", BOOL,
             "Serve a mobile-friendly control page on your local network",
             tooltip="When ON, a lightweight web page is hosted so you can\n"
                     "monitor and control the bot from any device on the same Wi-Fi.\n"
                     "Restart the app after changing this setting.")
        _row("remote.port", "Dashboard Port", TEXT,
             "Local port number the dashboard is served on  (default 8765)",
             tooltip="The port the web server listens on.\n"
                     "Open http://<your-PC-IP>:<port> in a phone browser.\n"
                     "Change only if the default port conflicts with another app.")
        # Restart notice — shown inline so users can't miss it
        _notice = tk.Frame(body, bg=_BG)
        _notice.pack(fill="x", padx=20, pady=(0, 6))
        tk.Label(_notice,
                 text="⚠  Changes to Remote Dashboard settings take effect on next launch.",
                 font=_F_SMALL, fg=_WARN, bg=_BG, anchor="w"
                 ).pack(side="left", padx=2)

        # ── Apply ─────────────────────────────────────────────────────────────
        HoverButton(body, text="APPLY SETTINGS",
                    command=self._save_config,
                    font=_F_HEAD, bg=_ACCENT_MU, fg=_ACCENT,
                    hover_bg=_ACCENT, hover_fg="#000000",
                    pady=13
                    ).pack(fill="x", padx=20, pady=20)

        # Propagate MouseWheel to the canvas so scrolling works over any child widget.
        # Also bind directly on canvas for the rare case the mouse is over empty canvas space.
        def _settings_scroll(e: tk.Event) -> None:
            canvas.yview_scroll(-1 * (e.delta // 120), "units")

        canvas.bind("<MouseWheel>", _settings_scroll)
        self._bind_scroll_tree(body, handler=_settings_scroll)

    # ── Logic Window ──────────────────────────────────────────────────────────
    def _open_logic_window(self, step_idx: int = -1):
        if step_idx < 0:
            return
        seq = self._cfg.get("macro", {}).get("sequence", [])
        if step_idx >= len(seq):
            return   # step was deleted between row build and click
        i = step_idx
        item = seq[i]
        is_string = isinstance(item, str)
        raw = item if is_string else item.get("template", "")

        cmd_type = None
        if raw.upper().startswith("POS:"): cmd_type = "POS"
        elif raw.upper().startswith("WAIT:"): cmd_type = "WAIT"
        elif raw.upper().startswith("KEY:"): cmd_type = "KEY"

        top = tk.Toplevel(self); top.title(f"Step {i+1} Editor")
        top.configure(bg=_BG_CARD)
        top.resizable(False, False)
        self._show_popup(top, 460, 370)

        # ── Header bar ────────────────────────────────────────────────────────
        hdr = tk.Frame(top, bg=_BG_INSET)
        hdr.pack(fill="x")
        tk.Label(hdr, text=f"  ✏  EDIT {cmd_type or 'TEMPLATE'} STEP",
                 bg=_BG_INSET, fg=_ACCENT, font=_F_TITLE, pady=12
                 ).pack(side="left", padx=16)
        tk.Label(hdr, text=f"Step #{i + 1}", bg=_BG_INSET, fg=_TEXT_DIM,
                 font=_F_SMALL).pack(side="right", padx=16)

        # ── Body fields ───────────────────────────────────────────────────────
        body = tk.Frame(top, bg=_BG_CARD)
        body.pack(fill="x", padx=20, pady=10)

        def _e_style(parent, var, width=None):
            kw = dict(textvariable=var, bg=_ENTRY_BG, fg=_TEXT,
                      insertbackground=_TEXT, relief="flat",
                      highlightthickness=1, highlightbackground=_ENTRY_BD,
                      highlightcolor=_ENTRY_FC, font=_F_MONO)
            if width: kw["width"] = width
            return tk.Entry(parent, **kw)

        def _field_lbl(parent, txt):
            tk.Label(parent, text=txt, bg=_BG_CARD, fg=_TEXT_MID,
                     font=_F_LABEL, anchor="w").pack(anchor="w", pady=(8, 2))

        # Main value field
        if cmd_type:
            val_v = tk.StringVar(value=raw.split(":", 1)[1])
            _field_lbl(body, f"ACTION VALUE  ({cmd_type})")
            _e_style(body, val_v).pack(fill="x", ipady=4)
        else:
            val_v = tk.StringVar(value=raw)
            _field_lbl(body, "TEMPLATE NAME")
            tpl_row = tk.Frame(body, bg=_BG_CARD)
            tpl_row.pack(fill="x")
            _e_style(tpl_row, val_v).pack(side="left", fill="x", expand=True, ipady=4)
            HoverButton(tpl_row, text="Browse…",
                        command=lambda: self._open_template_picker(
                            lambda f: val_v.set(f), confirm_label="USE"),
                        font=_F_SMALL, bg=_BG_HL, fg=_ACCENT,
                        hover_bg=_ACCENT, hover_fg="#000",
                        pady=5, padx=10).pack(side="left", padx=(6, 0))

        label_v = tk.StringVar(value=item.get("label", "") if not is_string else "")
        _field_lbl(body, "LABEL / NOTE  (optional)")
        _e_style(body, label_v).pack(fill="x", ipady=4)

        # ── Divider ───────────────────────────────────────────────────────────
        tk.Frame(top, bg=_BORDER_LT, height=1).pack(fill="x", pady=(6, 0))

        # ── Advanced toggle ───────────────────────────────────────────────────
        adv_open  = [False]
        adv_frame = tk.Frame(top, bg=_BG_INSET, pady=8)

        def _toggle_adv():
            if adv_open[0]:
                adv_frame.pack_forget()
                adv_open[0] = False
                adv_btn.config(text="⚙  ADVANCED LOGIC & CYCLES  ➕")
                self._show_popup(top, 460, 370)
            else:
                adv_frame.pack(fill="x", before=foot)
                adv_open[0] = True
                adv_btn.config(text="⚙  ADVANCED LOGIC & CYCLES  ➖")
                self._show_popup(top, 460, 610)

        adv_btn = HoverButton(top, text="⚙  ADVANCED LOGIC & CYCLES  ➕",
                              command=_toggle_adv,
                              bg=_BG_CARD, fg=_TEXT_DIM, font=_F_SMALL,
                              borderwidth=0, pady=8)
        adv_btn.pack(fill="x")

        # ── Advanced fields (2-column grid) ───────────────────────────────────
        adv_frame.columnconfigure(0, weight=1)
        adv_frame.columnconfigure(1, weight=0)

        def _adv_lbl(row, col, txt):
            tk.Label(adv_frame, text=txt, bg=_BG_INSET, fg=_TEXT_MID,
                     font=_F_LABEL, anchor="w"
                     ).grid(row=row, column=col, sticky="w",
                            padx=(16, 8), pady=(8, 0))

        def _adv_entry(row, col, var, width=8):
            e = tk.Entry(adv_frame, textvariable=var, width=width,
                         bg=_ENTRY_BG, fg=_TEXT, insertbackground=_TEXT,
                         relief="flat", highlightthickness=1,
                         highlightbackground=_ENTRY_BD, highlightcolor=_ENTRY_FC,
                         font=_F_MONO)
            e.grid(row=row, column=col, sticky="ew",
                   padx=(16, 8), pady=(2, 4), ipady=3)
            return e

        cond_v  = tk.StringVar(value=item.get("if_visible",  "") if not is_string else "")
        jump_v  = tk.StringVar(value=str(item.get("then_jump",   0)) if not is_string else "0")
        mode_v  = tk.StringVar(value=item.get("click_mode", "global") if not is_string else "global")
        cycle_v = tk.StringVar(value=str(item.get("cycle_count", 1)) if not is_string else "1")
        cd_v    = tk.StringVar(value=str(item.get("cooldown_s", 0.0)) if not is_string else "0.0")

        _adv_lbl(0, 0, "IF VISIBLE (template):")
        _adv_lbl(0, 1, "JUMP TO STEP:")
        e_cond = _adv_entry(1, 0, cond_v, width=22)
        e_jump = _adv_entry(1, 1, jump_v, width=6)
        Tooltip(e_cond, "If this template is detected on screen, jump to the specified step")
        Tooltip(e_jump, "Step number to jump to (1-based) when IF condition is met")

        _adv_lbl(2, 0, "CLICK MODE:")
        _adv_lbl(2, 1, "CYCLE COUNT:")
        cb = ttk.Combobox(adv_frame, textvariable=mode_v, values=["local", "global"],
                          state="readonly", width=12)
        cb.grid(row=3, column=0, sticky="ew", padx=(16, 8), pady=(2, 4), ipady=2)
        _adv_entry(3, 1, cycle_v, width=6)
        Tooltip(cb, "local = window-relative;  global = absolute screen coordinates")

        _adv_lbl(4, 0, "COOLDOWN (seconds):")
        e_cd = _adv_entry(5, 0, cd_v, width=10)
        Tooltip(e_cd, "Extra pause after this step completes (e.g. 0.5)")

        # ── Footer: Cancel / Save ─────────────────────────────────────────────
        foot = tk.Frame(top, bg=_BG_INSET)
        foot.pack(fill="x", side="bottom")

        def _save_step():
            final_tpl = f"{cmd_type}:{val_v.get()}" if cmd_type else val_v.get()
            lbl = label_v.get().strip() or None
            iv  = cond_v.get().strip() or None
            tj  = int(jump_v.get()) if jump_v.get().isdigit() else 0
            cm  = mode_v.get()
            cc  = int(cycle_v.get()) if cycle_v.get().isdigit() else 1
            cd  = float(cd_v.get()) if cd_v.get().replace('.', '', 1).isdigit() else 0.0
            is_simple = (not lbl and not iv and tj == 0 and cm == "global" and cc == 1 and cd == 0.0)
            if is_simple and cmd_type:
                new_item = final_tpl
            else:
                new_item = item if not isinstance(item, str) else {"template": final_tpl}
                new_item.update({"template": final_tpl, "label": lbl, "if_visible": iv,
                                 "then_jump": tj, "click_mode": cm, "cycle_count": cc,
                                 "cooldown_s": cd})
            self._cfg["macro"]["sequence"][i] = new_item
            self._save_cfg_silent(); self._refresh_macro_list(); top.destroy()

        HoverButton(foot, text="CANCEL", command=top.destroy,
                    bg=_BG_INSET, fg=_TEXT_DIM, font=_F_BODY,
                    hover_bg=_BG_HL, hover_fg=_TEXT,
                    pady=10, padx=24).pack(side="left", padx=(16, 0), pady=10)
        HoverButton(foot, text="✓  SAVE STEP CHANGES", command=_save_step,
                    bg=_ACCENT, fg="#0A0E1A", font=_F_HEAD,
                    hover_bg="#67E8F9", hover_fg="#0A0E1A",
                    pady=10).pack(side="right", fill="x", expand=True,
                                  padx=16, pady=10)

        top.bind("<Return>", lambda _: _save_step())
        top.bind("<Escape>", lambda _: top.destroy())

    # ── Sub-Loop Window ───────────────────────────────────────────────────────
    def _open_sub_loop_window(self):
        top = tk.Toplevel(self); top.title("Cyclic Sub-Loop Configuration")
        top.configure(bg=_BG_CARD)
        self._show_popup(top, 450, 550)
        tk.Label(top, text="AUTOMATED SUB-LOOP (Cyclic)",
                 bg=_BG_CARD, fg=_ACCENT, font=_F_TITLE).pack(pady=10)

        sl = self._cfg["macro"].setdefault(
            "sub_loop", {"enabled": False, "trigger_every": 10, "run_for": 2, "sequence": []})

        en_v = tk.BooleanVar(value=sl.get("enabled", False))
        _sl_init = en_v.get()
        _sl_row = tk.Frame(top, bg=_BG_CARD)
        _sl_row.pack(pady=10)
        _sl_lbl = tk.Label(_sl_row,
                           text="✓" if _sl_init else "✗",
                           width=2,
                           fg="#10B981" if _sl_init else "#64748B",
                           bg="#0B2E1E" if _sl_init else "#0D111E",
                           font=("Segoe UI", 10, "bold"), relief="solid", bd=1,
                           cursor="hand2", padx=2, pady=0)
        _sl_lbl.pack(side="left")
        tk.Label(_sl_row, text="  ENABLE SUB-LOOP", font=_F_HEAD,
                 fg=_ACCENT, bg=_BG_CARD, cursor="hand2").pack(side="left")

        def _toggle_sl(lbl=_sl_lbl, var=en_v):
            new_val = not var.get()
            var.set(new_val)
            if new_val:
                lbl.config(text="✓", fg="#10B981", bg="#0B2E1E")
            else:
                lbl.config(text="✗", fg="#64748B", bg="#0D111E")

        _sl_lbl.bind("<Button-1>", lambda e: _toggle_sl() or "break")
        _sl_row.bind("<Button-1>", lambda e: _toggle_sl())

        f = tk.Frame(top, bg=_BG_CARD); f.pack(pady=10)
        tk.Label(f, text="Trigger every", bg=_BG_CARD, fg=_TEXT).pack(side="left")
        trig_v = tk.StringVar(value=str(sl.get("trigger_every", 10)))
        tk.Entry(f, textvariable=trig_v, width=5).pack(side="left", padx=5)
        tk.Label(f, text="main cycles,", bg=_BG_CARD, fg=_TEXT).pack(side="left")
        tk.Label(f, text="run for", bg=_BG_CARD, fg=_TEXT).pack(side="left", padx=(10, 0))
        dur_v = tk.StringVar(value=str(sl.get("run_for", 2)))
        tk.Entry(f, textvariable=dur_v, width=5).pack(side="left", padx=5)
        tk.Label(f, text="cycles.", bg=_BG_CARD, fg=_TEXT).pack(side="left")

        tk.Label(top, text="SUB-MACRO SEQUENCE:",
                 bg=_BG_CARD, fg=_TEXT_MID, font=("Segoe UI", 9, "bold")).pack(pady=(20, 5))
        lb = tk.Listbox(top, bg=_BG, fg=_TEXT, font=("Consolas", 10), height=8)
        lb.pack(padx=20, fill="both", expand=True)
        for s in sl.get("sequence", []):
            lb.insert("end", s)

        btn_f = tk.Frame(top, bg=_BG_CARD); btn_f.pack(pady=10)
        HoverButton(btn_f, text="🔴 RECORD SUB",
                    command=lambda: self._macro_record_sub(lb),
                    bg=_BG_HL, fg=_RED, padx=15).pack(side="left", padx=5)
        HoverButton(btn_f, text="🗑️ CLEAR",
                    command=lambda: lb.delete(0, "end"),
                    bg=_BG_HL, fg=_TEXT, padx=15).pack(side="left", padx=5)

        def _save():
            sl["enabled"]       = en_v.get()
            sl["trigger_every"] = int(trig_v.get()) if trig_v.get().isdigit() else 10
            sl["run_for"]       = int(dur_v.get())  if dur_v.get().isdigit()  else 2
            sl["sequence"]      = list(lb.get(0, "end"))
            self._save_cfg_silent(); self._update_subloop_btn(); top.destroy()

        HoverButton(top, text="APPLY SUB-LOOP SETTINGS",
                    bg=_ACCENT, fg="#000", command=_save, pady=12).pack(pady=20, padx=20, fill="x")

    def _update_subloop_btn(self):
        sl = self._cfg.get("macro", {}).get("sub_loop", {})
        if sl.get("enabled"):
            self._btn_subloop.config(
                text=f"↺  SUB-LOOP: {sl.get('trigger_every')}/{sl.get('run_for')}",
                fg=_ACCENT)
        else:
            self._btn_subloop.config(text="↺  SUB-LOOP: OFF", fg=_TEXT_MID)

    # ── Triggers Window ───────────────────────────────────────────────────────
    def _open_triggers_window(self):
        top = tk.Toplevel(self); top.title("Global Interrupt Triggers")
        top.configure(bg=_BG_CARD)
        self._show_popup(top, 580, 480)

        # ── Header ────────────────────────────────────────────────────────────
        hdr = tk.Frame(top, bg=_BG_INSET)
        hdr.pack(fill="x")
        tk.Label(hdr, text="  ⚡  GLOBAL INTERRUPT TRIGGERS",
                 bg=_BG_INSET, fg=_ACCENT, font=_F_TITLE, pady=12
                 ).pack(side="left", padx=16)
        tk.Label(hdr, text="Run in parallel with the macro",
                 bg=_BG_INSET, fg=_TEXT_DIM, font=_F_SMALL
                 ).pack(side="right", padx=16)

        # ── Scrollable trigger list ───────────────────────────────────────────
        list_outer = tk.Frame(top, bg=_BG_CARD)
        list_outer.pack(fill="both", expand=True, padx=14, pady=10)

        t_canvas = tk.Canvas(list_outer, bg=_BG_CARD, highlightthickness=0)
        t_sb = tk.Scrollbar(list_outer, orient="vertical", command=t_canvas.yview,
                            bg=_BG_INSET, troughcolor=_BG_INSET)
        t_canvas.configure(yscrollcommand=t_sb.set)
        t_sb.pack(side="right", fill="y")
        t_canvas.pack(side="left", fill="both", expand=True)

        rows_frame = tk.Frame(t_canvas, bg=_BG_CARD)
        t_canvas.create_window((0, 0), window=rows_frame, anchor="nw")
        rows_frame.bind("<Configure>",
                        lambda e: t_canvas.configure(
                            scrollregion=t_canvas.bbox("all")))
        rows_frame.bind("<MouseWheel>",
                        lambda e: t_canvas.yview_scroll(
                            -1 * (e.delta // 120), "units"))

        def _refresh():
            for w in rows_frame.winfo_children():
                w.destroy()
            intrs = self._cfg.get("macro", {}).get("interrupts", [])
            if not intrs:
                tk.Label(rows_frame,
                         text="No triggers yet — click ADD TRIGGER below",
                         bg=_BG_CARD, fg=_TEXT_DIM, font=_F_BODY, pady=30
                         ).pack()
                return
            for idx, intr in enumerate(intrs):
                is_exec  = "execute_templates" in intr
                row_bg   = _BG_INSET if idx % 2 == 0 else _BG_CARD
                row      = tk.Frame(rows_frame, bg=row_bg)
                row.pack(fill="x", pady=1)
                # colour bar
                tk.Frame(row, bg=_SUCCESS if is_exec else _ACCENT,
                         width=4).pack(side="left", fill="y")
                # IF condition column
                cf = tk.Frame(row, bg=row_bg)
                cf.pack(side="left", fill="x", expand=True, padx=(8, 0), pady=6)
                tk.Label(cf, text="IF VISIBLE", bg=row_bg, fg=_TEXT_DIM,
                         font=_F_LABEL).pack(anchor="w")
                tk.Label(cf, text=intr.get("if_visible", "?"), bg=row_bg,
                         fg=_TEXT, font=_F_MONO).pack(anchor="w")
                # Action column
                af = tk.Frame(row, bg=row_bg)
                af.pack(side="left", fill="x", expand=True, padx=8, pady=6)
                if is_exec:
                    a_lbl = "EXECUTE"; a_val = ", ".join(intr.get("execute_templates", []))
                    a_clr = _SUCCESS
                else:
                    a_lbl = "JUMP TO STEP"; a_val = str(intr.get("trigger_step", "?"))
                    a_clr = _ACCENT
                tk.Label(af, text=a_lbl, bg=row_bg, fg=_TEXT_DIM,
                         font=_F_LABEL).pack(anchor="w")
                tk.Label(af, text=a_val, bg=row_bg, fg=a_clr,
                         font=_F_MONO).pack(anchor="w")
                # Duration column
                df = tk.Frame(row, bg=row_bg)
                df.pack(side="left", padx=8, pady=6)
                tk.Label(df, text="FOR", bg=row_bg, fg=_TEXT_DIM,
                         font=_F_LABEL).pack(anchor="w")
                tk.Label(df, text=f"{intr.get('duration_s', 5.0)}s",
                         bg=row_bg, fg=_TEXT, font=_F_MONO).pack(anchor="w")
                # Delete button
                def _del(bound_idx=idx):
                    self._cfg.get("macro", {}).get("interrupts", []).pop(bound_idx)
                    self._save_cfg_silent(); _refresh()
                HoverButton(row, text="✕", command=_del,
                            bg=row_bg, fg=_DANGER, font=_F_HEAD,
                            hover_bg=_DANGER, hover_fg="#000",
                            padx=10, pady=4).pack(side="right", padx=8, pady=6)

        # ── Add trigger dialog ─────────────────────────────────────────────────
        def _add():
            dlg = tk.Toplevel(top); dlg.title("Add Trigger")
            dlg.configure(bg=_BG_CARD); dlg.resizable(False, False)
            self._show_popup(dlg, 460, 440)

            dhdr = tk.Frame(dlg, bg=_BG_INSET)
            dhdr.pack(fill="x")
            tk.Label(dhdr, text="  ⚡  NEW TRIGGER RULE",
                     bg=_BG_INSET, fg=_ACCENT, font=_F_TITLE, pady=12
                     ).pack(side="left", padx=16)

            dbody = tk.Frame(dlg, bg=_BG_CARD)
            dbody.pack(fill="x", padx=20, pady=8)

            def _dlbl(txt):
                tk.Label(dbody, text=txt, bg=_BG_CARD, fg=_TEXT_MID,
                         font=_F_LABEL, anchor="w").pack(anchor="w", pady=(8, 2))

            def _de(parent, var):
                e = tk.Entry(parent, textvariable=var, bg=_ENTRY_BG, fg=_TEXT,
                             insertbackground=_TEXT, relief="flat",
                             highlightthickness=1, highlightbackground=_ENTRY_BD,
                             highlightcolor=_ENTRY_FC, font=_F_MONO)
                e.pack(fill="x", ipady=4)
                return e

            vis_v = tk.StringVar()
            _dlbl("IF VISIBLE (template name):")
            vr = tk.Frame(dbody, bg=_BG_CARD); vr.pack(fill="x")
            ve = tk.Entry(vr, textvariable=vis_v, bg=_ENTRY_BG, fg=_TEXT,
                          insertbackground=_TEXT, relief="flat",
                          highlightthickness=1, highlightbackground=_ENTRY_BD,
                          highlightcolor=_ENTRY_FC, font=_F_MONO)
            ve.pack(side="left", fill="x", expand=True, ipady=4)
            HoverButton(vr, text="Browse…",
                        command=lambda: self._open_template_picker(
                            lambda f: vis_v.set(f), confirm_label="USE"),
                        font=_F_SMALL, bg=_BG_HL, fg=_ACCENT,
                        hover_bg=_ACCENT, hover_fg="#000",
                        pady=5, padx=8).pack(side="left", padx=(6, 0))

            tk.Frame(dbody, bg=_BORDER_LT, height=1).pack(fill="x", pady=(10, 2))
            tk.Label(dbody, text="Fill Step OR Execute — not both",
                     bg=_BG_CARD, fg=_TEXT_DIM, font=_F_SMALL).pack(anchor="w")

            step_v = tk.StringVar()
            _dlbl("[A]  JUMP TO STEP (number):")
            _de(dbody, step_v)

            exec_v = tk.StringVar()
            _dlbl("[B]  EXECUTE TEMPLATE(S) (comma-separated):")
            er = tk.Frame(dbody, bg=_BG_CARD); er.pack(fill="x")
            ee = tk.Entry(er, textvariable=exec_v, bg=_ENTRY_BG, fg=_TEXT,
                          insertbackground=_TEXT, relief="flat",
                          highlightthickness=1, highlightbackground=_ENTRY_BD,
                          highlightcolor=_ENTRY_FC, font=_F_MONO)
            ee.pack(side="left", fill="x", expand=True, ipady=4)
            HoverButton(er, text="Browse…",
                        command=lambda: self._open_template_picker(
                            lambda f: exec_v.set(
                                (exec_v.get().rstrip(", ") + ", " + f).lstrip(", ")),
                            confirm_label="ADD"),
                        font=_F_SMALL, bg=_BG_HL, fg=_ACCENT,
                        hover_bg=_ACCENT, hover_fg="#000",
                        pady=5, padx=8).pack(side="left", padx=(6, 0))

            dur_v = tk.StringVar(value="5.0")
            _dlbl("DURATION (seconds):")
            _de(dbody, dur_v)

            dfoot = tk.Frame(dlg, bg=_BG_INSET)
            dfoot.pack(fill="x", side="bottom")

            def _save():
                dur = (float(dur_v.get())
                       if dur_v.get().replace('.', '', 1).isdigit() else 5.0)
                new_intr = {"if_visible": vis_v.get(), "duration_s": dur}
                exec_txt = exec_v.get().strip()
                if exec_txt:
                    new_intr["execute_templates"] = [
                        x.strip() for x in exec_txt.split(",") if x.strip()]
                else:
                    new_intr["trigger_step"] = (
                        int(step_v.get()) if step_v.get().isdigit() else 1)
                self._cfg.setdefault("macro", {}).setdefault(
                    "interrupts", []).append(new_intr)
                self._save_cfg_silent(); _refresh(); dlg.destroy()

            HoverButton(dfoot, text="CANCEL", command=dlg.destroy,
                        bg=_BG_INSET, fg=_TEXT_DIM, font=_F_BODY,
                        hover_bg=_BG_HL, hover_fg=_TEXT,
                        pady=10, padx=24).pack(side="left", padx=(16, 0), pady=10)
            HoverButton(dfoot, text="✓  ADD TRIGGER", command=_save,
                        bg=_ACCENT, fg="#0A0E1A", font=_F_HEAD,
                        hover_bg="#67E8F9", hover_fg="#0A0E1A",
                        pady=10).pack(side="right", fill="x", expand=True,
                                      padx=16, pady=10)
            dlg.bind("<Return>", lambda _: _save())
            dlg.bind("<Escape>", lambda _: dlg.destroy())

        # ── Footer with Add button ─────────────────────────────────────────────
        foot = tk.Frame(top, bg=_BG_INSET)
        foot.pack(fill="x", side="bottom")
        HoverButton(foot, text="＋  ADD TRIGGER", command=_add,
                    bg=_ACCENT, fg="#0A0E1A", font=_F_HEAD,
                    hover_bg="#67E8F9", hover_fg="#0A0E1A",
                    pady=10).pack(fill="x", padx=16, pady=10)

        _refresh()

    # ── Config helpers ────────────────────────────────────────────────────────
    def _get_cfg(self, k):
        d = self._cfg
        for p in k.split("."):
            if isinstance(d, dict): d = d.get(p, "")
            else: return ""
        return d

    def _refresh_macro_list(self) -> None:
        """Destroy and rebuild all step rows to reflect the current sequence."""
        if not hasattr(self, "_step_rows_frame") or self._step_rows_frame is None:
            return

        seq = self._cfg.get("macro", {}).get("sequence", [])
        q   = (self._macro_search_var.get() if self._macro_search_var else "").lower().strip()

        # ── While bot is running: only update the filter-count badge ─────────
        # Rebuilding rows would reset _step_active_bars and kill the step highlight.
        if self._is_running:
            if self._lbl_filter_count:
                if q:
                    shown = sum(
                        1 for item in seq
                        if q in (
                            f"{item if isinstance(item, str) else item.get('template', '')}"
                            f" {'' if isinstance(item, str) else (item.get('label', '') or '')}"
                        ).lower()
                    )
                    self._lbl_filter_count.config(
                        text=f"{shown} / {len(seq)} shown",
                        fg=_ACCENT if shown else _DANGER)
                else:
                    self._lbl_filter_count.config(text="")
            return

        # ── Full rebuild (bot not running) ────────────────────────────────────
        self._step_row_frames = []   # reset drag-drop registry
        self._drag_idx = None        # cancel any in-progress drag
        self._drag_drop_line = None  # indicator widget is destroyed with its parent
        self._step_active_bars = []  # rebuild active-step indicator bars
        for w in self._step_rows_frame.winfo_children():
            w.destroy()
        shown = 0
        for i, item in enumerate(seq):
            if q:
                raw = item if isinstance(item, str) else item.get("template", "")
                lbl = "" if isinstance(item, str) else (item.get("label", "") or "")
                if q not in f"{raw} {lbl}".lower():
                    continue
            self._build_step_row(i, item, len(seq))
            shown += 1
        # Update filter count label
        if self._lbl_filter_count:
            if q:
                self._lbl_filter_count.config(
                    text=f"{shown} / {len(seq)} shown", fg=_ACCENT if shown else _DANGER)
            else:
                self._lbl_filter_count.config(text="")
        self._step_rows_frame.update_idletasks()
        if hasattr(self, "_macro_canvas") and self._macro_canvas:
            self._macro_canvas.configure(scrollregion=self._macro_canvas.bbox("all"))
        self._update_est_time()

    def _entry(self, parent, var, width, color, row_bg):
        """Styled Entry: visible border, colored text, focus ring."""
        e = tk.Entry(parent, textvariable=var, width=width,
                     bg=_ENTRY_BG, fg=color,
                     font=_F_MONO, insertbackground=color,
                     relief="flat", justify="center",
                     highlightthickness=1,
                     highlightbackground=_ENTRY_BD,
                     highlightcolor=color)
        return e

    def _badge(self, parent, text, bg, fg):
        """Colored pill-style badge label."""
        return tk.Label(parent, text=f"  {text}  ", bg=bg, fg=fg,
                        font=_F_LABEL, pady=3)

    def _bind_scroll_tree(self, widget: tk.Widget, handler=None) -> None:
        """Recursively bind MouseWheel to *handler* (or self._scroll_handler) on every widget in the tree."""
        h = handler if handler is not None else getattr(self, "_scroll_handler", None)
        if h:
            widget.bind("<MouseWheel>", h, add="+")
        for child in widget.winfo_children():
            self._bind_scroll_tree(child, handler=h)

    def _build_step_row(self, i: int, item, total: int) -> None:
        """Render one macro sequence step row (POS / WAIT / KEY / TPL / IF) into the scroll frame."""
        raw    = item if isinstance(item, str) else item.get("template", "")
        cmd    = str(raw).upper().strip()
        row_bg = _ROW_A if i % 2 == 0 else _ROW_B

        row = tk.Frame(self._step_rows_frame, bg=row_bg, pady=7)
        row.pack(fill="x")
        tk.Frame(self._step_rows_frame, bg=_BORDER_LT, height=1).pack(fill="x")
        self._step_row_frames.append(row)  # register for drag-drop

        # Active-step highlight bar (3 px left strip; lit cyan while engine runs this step)
        act_bar = tk.Frame(row, bg=row_bg, width=3)
        act_bar.pack(side="left", fill="y")
        act_bar._normal_bg = row_bg  # type: ignore[attr-defined]

        # Right-click context menu
        row.bind("<Button-3>", lambda e, idx=i: self._show_step_ctx_menu(idx, e))

        # Drag-and-drop grip handle — disabled when a search filter is active
        # (filtered _step_row_frames is a partial list; drag indices would mismatch)
        filter_on = bool(self._macro_search_var and self._macro_search_var.get().strip())
        grip = tk.Label(row, text="⠿", bg=row_bg,
                        fg=_TEXT_DIM if not filter_on else _BG_HL,
                        font=_F_SMALL,
                        cursor="fleur" if not filter_on else "arrow",
                        padx=4)
        grip.pack(side="left", padx=(4, 0))
        if not filter_on:
            grip.bind("<Button-1>",       lambda e, idx=i: self._drag_start(idx, e))
            grip.bind("<B1-Motion>",      self._drag_motion)
            grip.bind("<ButtonRelease-1>",self._drag_end)
        else:
            Tooltip(grip, "Clear the search filter to enable drag-and-drop reordering")

        # Step number
        tk.Label(row, text=f"{i+1:>2}", width=3, bg=row_bg, fg=_TEXT_MID,
                 font=("Consolas", 9, "bold"), anchor="e"
                 ).pack(side="left", padx=(10, 6))

        # Enable/disable toggle — custom label replaces the native Checkbutton
        # which is invisible on dark themes (selectcolor blends into background).
        # Shows ✓ on green when enabled, ✗ on dim when disabled.
        is_enabled = item.get("enabled", True) if isinstance(item, dict) else True
        en_var = tk.BooleanVar(value=is_enabled)

        _TOG_ON  = ("✓", "#10B981", "#0B2E1E")   # symbol, fg, bg  — enabled
        _TOG_OFF = ("✗", "#64748B", "#0D111E")   # symbol, fg, bg  — disabled

        sym0, fg0, bg0 = _TOG_ON if is_enabled else _TOG_OFF
        en_lbl = tk.Label(
            row, text=sym0, width=2,
            fg=fg0, bg=bg0,
            font=("Segoe UI", 10, "bold"),
            relief="solid", bd=1,
            cursor="hand2", padx=2, pady=0,
        )
        en_lbl.pack(side="left", padx=(0, 6))

        def _toggle_en(lbl=en_lbl, ev=en_var, r=row, ab=act_bar):
            new_val = not ev.get()
            ev.set(new_val)                   # fires _save_enabled via trace
            sym, fg, bg = _TOG_ON if new_val else _TOG_OFF
            lbl.config(text=sym, fg=fg, bg=bg)
            # Dim the whole row when disabled; brighten when re-enabled
            new_row_bg = row_bg if new_val else _BG_INSET
            try:
                r.config(bg=new_row_bg)
                ab._normal_bg = new_row_bg    # type: ignore[attr-defined]
                for child in r.winfo_children():
                    try: child.config(bg=new_row_bg)
                    except Exception: pass
                lbl.config(bg=bg)             # restore toggle's own distinct bg
            except Exception:
                pass

        en_lbl.bind("<Button-1>", lambda e, f=_toggle_en: f())

        def _save_enabled(idx=i, ev=en_var, orig=item):
            seq = self._cfg["macro"]["sequence"]
            try:
                cur = seq[idx]
                if isinstance(cur, dict):
                    cur["enabled"] = ev.get()
                else:
                    seq[idx] = {"template": cur, "enabled": ev.get()}
                self._save_cfg_silent()
            except (IndexError, Exception) as e:
                _log.warning("Could not save enabled state for step %d: %s", idx, e)

        en_var.trace_add("write", lambda *_, f=_save_enabled: f())

        if not is_enabled:
            row.config(bg=_BG_INSET)
            act_bar._normal_bg = _BG_INSET  # type: ignore[attr-defined]
            for child in row.winfo_children():
                try: child.config(bg=_BG_INSET)
                except Exception: pass
            en_lbl.config(bg=_TOG_OFF[2])    # keep toggle bg after row sweep

        self._step_active_bars.append(act_bar)

        if cmd.startswith("POS:"):
            parts = cmd[4:].split(",")
            px  = parts[0].strip() if len(parts) > 0 else "0"
            py  = parts[1].strip() if len(parts) > 1 else "0"
            glb = len(parts) > 2 and parts[2].strip() == "GLOBAL"
            lbl_val = item.get("label", "") if isinstance(item, dict) else ""

            self._badge(row, "POS", _ORANGE, "#000").pack(side="left", padx=(0, 8))

            # Name / label field
            lbl_var = tk.StringVar(value=lbl_val)
            lbl_e = self._entry(row, lbl_var, 14, _TEXT_MID, row_bg)
            lbl_e.configure(highlightbackground=_BORDER, fg=_TEXT_MID)
            lbl_e.pack(side="left", padx=(0, 10))
            # Separator
            tk.Frame(row, bg=_BORDER_LT, width=1, height=22
                     ).pack(side="left", padx=(0, 10))

            # Coordinates
            tk.Label(row, text="X", bg=row_bg, fg=_TEXT_MID,
                     font=_F_LABEL).pack(side="left")
            x_var = tk.StringVar(value=px)
            self._entry(row, x_var, 6, _ORANGE, row_bg).pack(side="left", padx=(3, 10))

            tk.Label(row, text="Y", bg=row_bg, fg=_TEXT_MID,
                     font=_F_LABEL).pack(side="left")
            y_var = tk.StringVar(value=py)
            self._entry(row, y_var, 6, _ORANGE, row_bg).pack(side="left", padx=(3, 12))

            g_var = tk.BooleanVar(value=glb)
            _gi_sym, _gi_fg, _gi_bg = ("✓", "#10B981", "#0B2E1E") if glb else ("✗", "#64748B", "#0D111E")
            _g_inline = tk.Label(row, text=_gi_sym, width=2, fg=_gi_fg, bg=_gi_bg,
                                 font=("Segoe UI", 9, "bold"), relief="solid", bd=1,
                                 cursor="hand2", padx=2, pady=0)
            _g_inline.pack(side="left")
            tk.Label(row, text=" GLOBAL", font=_F_SMALL, fg=_TEXT_MID,
                     bg=row_bg, cursor="hand2").pack(side="left")

            def _toggle_ginline(lbl=_g_inline, var=g_var):
                new_val = not var.get()
                var.set(new_val)
                if new_val:
                    lbl.config(text="✓", fg="#10B981", bg="#0B2E1E")
                else:
                    lbl.config(text="✗", fg="#64748B", bg="#0D111E")

            _g_inline.bind("<Button-1>", lambda e: _toggle_ginline())

            def _save_pos(idx=i, xv=x_var, yv=y_var, gv=g_var,
                          lv=lbl_var, orig=item):
                suffix  = ",GLOBAL" if gv.get() else ""
                pos_str = f"POS:{xv.get()},{yv.get()}{suffix}"
                lbl     = lv.get().strip()
                try:
                    if lbl or isinstance(orig, dict):
                        base = dict(orig) if isinstance(orig, dict) else {}
                        base["template"] = pos_str
                        if lbl:
                            base["label"] = lbl
                        else:
                            base.pop("label", None)
                        self._cfg["macro"]["sequence"][idx] = base
                    else:
                        self._cfg["macro"]["sequence"][idx] = pos_str
                    self._save_cfg_silent()
                except Exception as e:
                    _log.warning("Could not save POS step %d: %s", idx, e)

            def _sync_g_visual(*_, lbl=_g_inline, var=g_var):
                if var.get():
                    lbl.config(text="✓", fg="#10B981", bg="#0B2E1E")
                else:
                    lbl.config(text="✗", fg="#64748B", bg="#0D111E")

            x_var.trace_add("write",   lambda *_, f=_save_pos: f())
            y_var.trace_add("write",   lambda *_, f=_save_pos: f())
            g_var.trace_add("write",   lambda *_, f=_save_pos: f())
            g_var.trace_add("write",   _sync_g_visual)
            lbl_var.trace_add("write", lambda *_, f=_save_pos: f())

            HoverButton(row, text="🎯 PICK",
                        command=lambda xv=x_var, yv=y_var, gv=g_var:
                            self._pick_pos(xv, yv, gv),
                        bg=_TB_POS, fg=_ORANGE,
                        hover_bg=_ORANGE, hover_fg="#000",
                        font=_F_SMALL, pady=4, padx=8
                        ).pack(side="left", padx=(8, 0))

        elif cmd.startswith("WAIT:"):
            w_val = str(raw).split(":", 1)[1].strip() if ":" in str(raw) else "1.0"

            self._badge(row, "WAIT", _YELLOW, "#000").pack(side="left", padx=(0, 10))

            w_var = tk.StringVar(value=w_val)
            self._entry(row, w_var, 8, _YELLOW, row_bg).pack(side="left", padx=(0, 6))
            tk.Label(row, text="seconds", bg=row_bg, fg=_TEXT_MID,
                     font=_F_MONOS).pack(side="left")

            def _save_wait(idx=i, wv=w_var):
                try:
                    self._cfg["macro"]["sequence"][idx] = f"WAIT:{wv.get()}"
                    self._save_cfg_silent()
                except Exception as e:
                    _log.warning("Could not save WAIT step %d: %s", idx, e)

            w_var.trace_add("write", lambda *_, f=_save_wait: f())

        elif cmd.startswith("KEY:"):
            k_val = str(raw).split(":", 1)[1].strip() if ":" in str(raw) else ""

            self._badge(row, "KEY", _PURPLE, "#fff").pack(side="left", padx=(0, 10))

            k_var = tk.StringVar(value=k_val)
            self._entry(row, k_var, 12, _PURPLE, row_bg).pack(side="left")

            def _save_key(idx=i, kv=k_var):
                try:
                    self._cfg["macro"]["sequence"][idx] = f"KEY:{kv.get().upper()}"
                    self._save_cfg_silent()
                except Exception as e:
                    _log.warning("Could not save KEY step %d: %s", idx, e)

            k_var.trace_add("write", lambda *_, f=_save_key: f())

        else:
            is_dict  = isinstance(item, dict)
            tpl_name = item.get("template", "") if is_dict else str(item)
            if_vis   = item.get("if_visible", "") if is_dict else ""
            then_jmp = item.get("then_jump", 0)   if is_dict else 0
            lbl_val  = item.get("label", "")       if is_dict else ""

            if if_vis:
                self._badge(row, "IF", _SUCCESS, "#000").pack(side="left", padx=(0, 10))
            else:
                self._badge(row, "TPL", _ACCENT_MU, _ACCENT).pack(side="left", padx=(0, 10))

            t_var = tk.StringVar(value=tpl_name)
            _tentry = self._entry(row, t_var, 18, _ACCENT, row_bg)
            _tentry.pack(side="left", padx=(0, 6))
            _tentry.bind("<Enter>", lambda e, n=tpl_name: self._show_tpl_thumb(e, n))
            _tentry.bind("<Leave>", self._hide_tpl_thumb)

            # Inline label entry
            lbl_var = tk.StringVar(value=lbl_val)
            lbl_e = self._entry(row, lbl_var, 12, _TEXT_MID, row_bg)
            lbl_e.configure(highlightbackground=_BORDER,
                            fg=_TEXT_MID if lbl_val else _TEXT_DIM)
            lbl_e.pack(side="left", padx=(0, 8))
            Tooltip(lbl_e, "Label / note for this step (optional)")

            if if_vis:
                tk.Label(row, text=f"→ step {then_jmp}",
                         bg=row_bg, fg=_ACCENT, font=_F_MONOS).pack(side="left", padx=(0, 6))

            def _save_tpl(idx=i, tv=t_var, lv=lbl_var, orig=item):
                try:
                    lbl = lv.get().strip()
                    if isinstance(orig, dict) or lbl:
                        new = dict(orig) if isinstance(orig, dict) else {}
                        new["template"] = tv.get()
                        if lbl:
                            new["label"] = lbl
                        else:
                            new.pop("label", None)
                        self._cfg["macro"]["sequence"][idx] = new
                    else:
                        self._cfg["macro"]["sequence"][idx] = tv.get()
                    self._save_cfg_silent()
                except Exception as e:
                    _log.warning("Could not save TPL step %d: %s", idx, e)

            t_var.trace_add("write",   lambda *_, f=_save_tpl: f())
            lbl_var.trace_add("write", lambda *_, f=_save_tpl: f())

            HoverButton(row, text="✦ ADV",
                        command=lambda idx=i: self._edit_step(idx),
                        bg=_ACCENT_MU, fg=_ACCENT,
                        hover_bg=_ACCENT, hover_fg="#000",
                        font=_F_SMALL, pady=4, padx=8
                        ).pack(side="left", padx=2)

            HoverButton(row, text="⬤ TEST",
                        command=lambda idx=i: self._macro_test_step(idx),
                        bg="#002A14", fg=_SUCCESS,
                        hover_bg=_SUCCESS, hover_fg="#000",
                        font=_F_SMALL, pady=4, padx=8
                        ).pack(side="left", padx=2)

        # Per-row controls — ↑↓ removed (use drag handle or right-click menu)
        ctrl_f = tk.Frame(row, bg=row_bg)
        ctrl_f.pack(side="right", padx=8)
        HoverButton(ctrl_f, text="✕",
                    command=lambda idx=i: self._step_delete(idx),
                    bg="#1E0008", fg=_DANGER,
                    hover_bg=_DANGER, hover_fg="#fff",
                    font=_F_SMALL, width=2, pady=4
                    ).pack(side="right", padx=2)
        HoverButton(ctrl_f, text="⧉",
                    command=lambda idx=i: self._step_duplicate(idx),
                    bg=row_bg, fg=_TEXT_MID,
                    hover_bg=_BORDER_LT, hover_fg=_ACCENT,
                    font=_F_SMALL, width=2, pady=4
                    ).pack(side="right", padx=1)

        # Propagate scroll to every widget in this row
        self._bind_scroll_tree(row)

    def _pick_window_title(self, var: tk.StringVar) -> None:
        """Open a popup listing all visible top-level windows for the user to pick from."""
        titles: list[str] = []
        def _cb(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                t = win32gui.GetWindowText(hwnd)
                if t.strip():
                    titles.append(t)
        win32gui.EnumWindows(_cb, None)
        titles.sort(key=str.lower)

        top = tk.Toplevel(self)
        top.title("Pick Target Window")
        top.configure(bg=_BG_CARD)
        top.geometry("420x380")
        top.grab_set()

        tk.Label(top, text="SELECT TARGET WINDOW", bg=_BG_CARD, fg=_ACCENT,
                 font=_F_HEAD).pack(pady=(12, 6))

        # Search filter
        search_var = tk.StringVar()
        search_e = tk.Entry(top, textvariable=search_var, bg=_BG_INSET, fg=_TEXT,
                            font=_F_MONO, relief="flat",
                            highlightthickness=1, highlightbackground=_BORDER,
                            highlightcolor=_ACCENT, insertbackground=_ACCENT)
        search_e.pack(fill="x", padx=12, pady=(0, 6))
        search_e.focus_set()

        lb = tk.Listbox(top, bg=_BG_INSET, fg=_TEXT, font=_F_MONO,
                        selectbackground=_ACCENT_MU, selectforeground=_ACCENT,
                        activestyle="none", borderwidth=0, highlightthickness=0)
        lb.pack(fill="both", expand=True, padx=12, pady=(0, 8))

        def _populate(filter_text=""):
            lb.delete(0, "end")
            f = filter_text.lower()
            for t in titles:
                if f in t.lower():
                    lb.insert("end", t)

        _populate()
        search_var.trace_add("write", lambda *_: _populate(search_var.get()))

        def _confirm():
            sel = lb.curselection()
            if sel:
                var.set(lb.get(sel[0]))
                top.destroy()

        lb.bind("<Double-Button-1>", lambda e: _confirm())

        HoverButton(top, text="SELECT", command=_confirm,
                    bg=_ACCENT_MU, fg=_ACCENT,
                    hover_bg=_ACCENT, hover_fg="#000",
                    font=_F_HEAD, pady=8
                    ).pack(fill="x", padx=12, pady=(0, 12))

    # ── Macro step operations ──────────────────────────────────────────────────
    def _pick_pos(self, x_var: tk.StringVar, y_var: tk.StringVar,
                  g_var: tk.BooleanVar) -> None:
        """
        Minimise the panel, show a floating banner, then capture the next
        screen click via pynput and write the coordinates into x_var / y_var.
        Coordinates are converted to client-relative if the target window is found
        (matching what the GLOBAL click mode expects). ESC cancels.
        """
        sw = self.winfo_screenwidth()

        # Floating instruction banner pinned to top-centre of screen
        banner = tk.Toplevel(self)
        banner.overrideredirect(True)
        banner.attributes("-topmost", True)
        banner.configure(bg=_ORANGE)
        bw, bh = 380, 48
        banner.geometry(f"{bw}x{bh}+{(sw - bw) // 2}+12")
        tk.Label(banner,
                 text="🎯  Click the target position on screen  ·  ESC to cancel",
                 bg=_ORANGE, fg="#000",
                 font=("Segoe UI", 9, "bold")).pack(expand=True)
        banner.update()

        self.iconify()   # get our own window out of the way

        result: list = [None]

        def _start_listener():
            def on_click(x, y, button, pressed):
                if not pressed or button != pynput_mouse.Button.left:
                    return
                # Ignore clicks on the banner itself
                try:
                    bx, by = banner.winfo_x(), banner.winfo_y()
                    if bx <= x <= bx + bw and by <= y <= by + bh:
                        return
                except Exception:
                    pass
                result[0] = (int(x), int(y))
                return False

            def on_key(key):
                if key == pynput_kb.Key.esc:
                    result[0] = "cancel"
                    return False

            _listeners: list = []   # [ml, kl] — stored so timeout can stop them

            def _run():
                ml = pynput_mouse.Listener(on_click=on_click)
                kl = pynput_kb.Listener(on_press=on_key)
                _listeners.extend([ml, kl])
                ml.start(); kl.start()
                ml.join(); kl.stop()
                self.after(0, _finish)

            threading.Thread(target=_run, daemon=True).start()

            def _timeout():
                """Kill the pick listeners if the user forgot about them."""
                if result[0] is None:   # not yet captured
                    result[0] = "cancel"
                    for lst in _listeners:
                        try: lst.stop()
                        except Exception: pass

            self.after(30_000, _timeout)

        def _finish():
            try:
                banner.destroy()
            except Exception:
                pass
            self.deiconify()
            self.lift()

            if not result[0] or result[0] == "cancel":
                return

            sx, sy = result[0]

            # Try to convert screen → client-relative for the target window
            wc = WindowController(self._cfg["window_title"])
            if wc.find_window():
                try:
                    cp = win32gui.ScreenToClient(wc.hwnd, (sx, sy))
                    x_var.set(str(cp[0]))
                    y_var.set(str(cp[1]))
                    g_var.set(True)
                    return
                except Exception:
                    pass

            # Fallback: store raw screen coords, disable GLOBAL flag
            x_var.set(str(sx))
            y_var.set(str(sy))
            g_var.set(False)

        # Small delay so the button-click that triggered us doesn't get captured
        self.after(250, _start_listener)

    # ── Undo / Redo ───────────────────────────────────────────────────────────
    def _push_undo(self) -> None:
        """Snapshot the current sequence onto the undo stack and clear the redo stack."""
        self._undo_stack.append(copy.deepcopy(self._cfg["macro"]["sequence"]))
        self._redo_stack.clear()
        self._update_undo_btns()

    def _undo(self) -> None:
        """Restore the previous sequence snapshot."""
        if not self._undo_stack:
            return
        self._redo_stack.append(copy.deepcopy(self._cfg["macro"]["sequence"]))
        self._cfg["macro"]["sequence"] = self._undo_stack.pop()
        self._save_cfg_silent()
        self._refresh_macro_list()
        self._update_undo_btns()

    def _redo(self) -> None:
        """Re-apply a previously undone change."""
        if not self._redo_stack:
            return
        self._undo_stack.append(copy.deepcopy(self._cfg["macro"]["sequence"]))
        self._cfg["macro"]["sequence"] = self._redo_stack.pop()
        self._save_cfg_silent()
        self._refresh_macro_list()
        self._update_undo_btns()

    def _update_undo_btns(self) -> None:
        """Enable/disable the UNDO and REDO toolbar buttons to reflect stack state."""
        if self._btn_undo:
            if self._undo_stack:
                self._btn_undo.set_style(_BG_HL, _TEXT)
                self._btn_undo.config(state="normal")
            else:
                self._btn_undo.set_style(_BG_HL, _TEXT_DIM)
                self._btn_undo.config(state="disabled")
        if self._btn_redo:
            if self._redo_stack:
                self._btn_redo.set_style(_BG_HL, _TEXT)
                self._btn_redo.config(state="normal")
            else:
                self._btn_redo.set_style(_BG_HL, _TEXT_DIM)
                self._btn_redo.config(state="disabled")

    # ── Step context menu (right-click) ───────────────────────────────────────
    def _show_step_ctx_menu(self, idx: int, event: tk.Event) -> None:
        """Show a right-click context menu for the given step row."""
        seq = self._cfg["macro"]["sequence"]
        menu = tk.Menu(self, tearoff=0, bg=_BG_CARD, fg=_TEXT,
                       activebackground=_ACCENT_MU, activeforeground=_ACCENT,
                       relief="flat", borderwidth=1)
        menu.add_command(label="✂  Cut",       command=lambda: self._step_cut(idx))
        menu.add_command(label="⧉  Copy",      command=lambda: self._step_copy(idx))
        has_clip = self._clipboard_step is not None
        menu.add_command(label="⬆  Paste Above", state="normal" if has_clip else "disabled",
                         command=lambda: self._step_paste(idx, after=False))
        menu.add_command(label="⬇  Paste Below", state="normal" if has_clip else "disabled",
                         command=lambda: self._step_paste(idx, after=True))
        menu.add_separator()
        menu.add_command(label="⧉  Duplicate",   command=lambda: self._step_duplicate(idx))
        menu.add_separator()
        menu.add_command(label="↑  Move Up",   state="normal" if idx > 0 else "disabled",
                         command=lambda: self._step_move(idx, -1))
        menu.add_command(label="↓  Move Down", state="normal" if idx < len(seq)-1 else "disabled",
                         command=lambda: self._step_move(idx, 1))
        menu.add_command(label="⬆  Move to Top",    state="normal" if idx > 0 else "disabled",
                         command=lambda: self._step_move_to(idx, 0))
        menu.add_command(label="⬇  Move to Bottom", state="normal" if idx < len(seq)-1 else "disabled",
                         command=lambda: self._step_move_to(idx, len(seq)-1))
        menu.add_separator()
        menu.add_command(label="✕  Delete", foreground=_DANGER,
                         command=lambda: self._step_delete(idx))
        menu.tk_popup(event.x_root, event.y_root)

    def _step_cut(self, idx: int) -> None:
        """Copy step to clipboard then delete it."""
        self._step_copy(idx)
        self._step_delete(idx)

    def _step_copy(self, idx: int) -> None:
        """Deep-copy the step at idx to the clipboard."""
        try:
            self._clipboard_step = copy.deepcopy(self._cfg["macro"]["sequence"][idx])
        except IndexError:
            pass

    def _step_paste(self, idx: int, after: bool = True) -> None:
        """Insert clipboard step above or below idx."""
        if self._clipboard_step is None:
            return
        self._push_undo()
        insert_at = idx + 1 if after else idx
        self._cfg["macro"]["sequence"].insert(insert_at, copy.deepcopy(self._clipboard_step))
        self._save_cfg_silent()
        self._refresh_macro_list()

    # ── Estimated loop time ───────────────────────────────────────────────────
    def _calc_est_loop_time(self) -> float:
        """Sum WAIT durations + click_delay per action step, scaled by playback speed."""
        seq     = self._cfg.get("macro", {}).get("sequence", [])
        delay_s = float(self._cfg.get("macro", {}).get("click_delay_ms", 500)) / 1000.0
        speed   = max(float(self._cfg.get("macro", {}).get("playback_speed", 1.0)), 0.1)
        total   = 0.0
        for step in seq:
            if isinstance(step, dict) and not step.get("enabled", True):
                continue  # skip disabled steps
            raw = step if isinstance(step, str) else (step.get("template") or "")
            if str(raw).upper().startswith("WAIT:"):
                try:
                    total += float(str(raw).split(":", 1)[1].strip())
                except (ValueError, IndexError):
                    pass
            else:
                total += delay_s
        return total / speed

    def _update_est_time(self) -> None:
        """Refresh the estimated loop time and step count footer labels."""
        seq = self._cfg.get("macro", {}).get("sequence", [])
        enabled = sum(1 for s in seq
                      if not (isinstance(s, dict) and not s.get("enabled", True)))
        total   = self._calc_est_loop_time()
        mm, ss  = divmod(int(total), 60)
        time_txt = f"{mm}m {ss}s" if mm else f"{total:.1f}s"
        if self._lbl_est_time:
            self._lbl_est_time.config(text=f"Est. loop: ~{time_txt}")
        if self._lbl_step_count:
            total_n = len(seq)
            disabled_n = total_n - enabled
            badge = f"{total_n} step{'s' if total_n != 1 else ''}"
            if disabled_n:
                badge += f"  ·  {disabled_n} disabled"
            self._lbl_step_count.config(text=badge)

    # ── Drag-and-drop reordering ──────────────────────────────────────────────
    def _drag_start(self, idx: int, event: tk.Event) -> None:
        """Record drag source index when the grip handle is pressed."""
        self._drag_idx = idx

    def _drag_motion(self, event: tk.Event) -> None:
        """Move the drop-indicator line to the current hover position."""
        if self._drag_idx is None:
            return
        frames = self._step_row_frames
        if not frames:
            return
        # Create indicator on first motion
        if self._drag_drop_line is None:
            self._drag_drop_line = tk.Frame(
                self._step_rows_frame, bg=_ACCENT, height=2)
        insert = self._drag_get_target(event.y_root)
        if insert < len(frames):
            y = frames[insert].winfo_y()
        else:
            last = frames[-1]
            y = last.winfo_y() + last.winfo_height()
        self._drag_drop_line.place(x=0, y=y, relwidth=1.0, height=2)
        self._drag_drop_line.lift()

    def _drag_end(self, event: tk.Event) -> None:
        """On mouse release: move the step to the drop position."""
        if self._drag_drop_line:
            self._drag_drop_line.place_forget()
        if self._drag_idx is None:
            return
        src    = self._drag_idx
        self._drag_idx = None
        insert = self._drag_get_target(event.y_root)
        # Convert insertion point → final position after pop
        final  = insert if insert <= src else insert - 1
        if final != src:
            self._step_move_to(src, final)

    def _drag_get_target(self, mouse_y_abs: int) -> int:
        """Return insertion index (0..len) based on absolute screen Y."""
        for i, frame in enumerate(self._step_row_frames):
            mid = frame.winfo_rooty() + frame.winfo_height() // 2
            if mouse_y_abs < mid:
                return i
        return len(self._step_row_frames)

    def _step_move_to(self, idx: int, target: int) -> None:
        """Move step at idx directly to target position."""
        seq = self._cfg["macro"]["sequence"]
        if idx == target or not (0 <= idx < len(seq)) or not (0 <= target < len(seq)):
            return
        self._push_undo()
        step = seq.pop(idx)
        seq.insert(target, step)
        self._save_cfg_silent()
        self._refresh_macro_list()

    # ── Step mutations ────────────────────────────────────────────────────────
    def _step_move(self, idx: int, direction: int) -> None:
        seq = self._cfg["macro"]["sequence"]
        new_idx = idx + direction
        if 0 <= new_idx < len(seq):
            self._push_undo()
            seq[idx], seq[new_idx] = seq[new_idx], seq[idx]
            self._save_cfg_silent()
            self._refresh_macro_list()

    def _step_delete(self, idx: int) -> None:
        try:
            self._push_undo()
            self._cfg["macro"]["sequence"].pop(idx)
            self._save_cfg_silent()
            self._refresh_macro_list()
        except IndexError:
            pass

    def _step_duplicate(self, idx: int) -> None:
        try:
            self._push_undo()
            seq = self._cfg["macro"]["sequence"]
            seq.insert(idx + 1, copy.deepcopy(seq[idx]))
            self._save_cfg_silent()
            self._refresh_macro_list()
        except IndexError:
            pass

    def _edit_step(self, idx: int) -> None:
        self._open_logic_window(idx)

    # ── Macro Export / Import ─────────────────────────────────────────────────
    def _macro_clear(self) -> None:
        """Prompt the user, then wipe the entire macro sequence."""
        if self._is_running:
            return
        seq = self._cfg.get("macro", {}).get("sequence", [])
        if not seq:
            self._lbl_status.config(text="⚠  Sequence is already empty.", fg=_WARN)
            self.after(3000, lambda: self._lbl_status.config(text="", fg=_TEXT_MID))
            return
        if not messagebox.askyesno(
            "Clear Sequence",
            f"Delete all {len(seq)} step{'s' if len(seq) != 1 else ''}?\n\nYou can undo this with Ctrl+Z.",
            icon="warning",
        ):
            return
        self._push_undo()
        self._cfg.setdefault("macro", {})["sequence"] = []
        self._save_cfg_silent()
        self._refresh_macro_list()
        self._lbl_status.config(text="🗑  Sequence cleared.", fg=_WARN)
        self.after(3000, lambda: self._lbl_status.config(text="", fg=_TEXT_MID))

    def _macro_export(self) -> None:
        """Export the current macro sequence steps to a standalone JSON file."""
        from tkinter import filedialog
        seq = self._cfg.get("macro", {}).get("sequence", [])
        if not seq:
            self._lbl_status.config(text="⚠  Nothing to export — sequence is empty.", fg=_WARN)
            self.after(4000, lambda: self._lbl_status.config(text="", fg=_TEXT_MID))
            return
        path = filedialog.asksaveasfilename(
            title="Export Macro Sequence",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            initialfile="macro_sequence.json",
        )
        if not path:
            return
        payload = {
            "chainex_export": True,
            "version": 1,
            "sequence": seq,
        }
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            _log.info("Macro sequence exported to %s (%d steps)", path, len(seq))
            self._lbl_status.config(
                text=f"✓ Exported {len(seq)} steps → {Path(path).name}", fg=_SUCCESS)
            self.after(4000, lambda: self._lbl_status.config(text="", fg=_TEXT_MID))
        except OSError as e:
            messagebox.showerror("Export Failed", str(e))

    def _macro_import(self) -> None:
        """Import macro sequence steps from a previously exported JSON file."""
        if self._is_running:
            return
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title="Import Macro Sequence",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            messagebox.showerror("Import Failed", f"Could not read file:\n{e}")
            return
        seq = data.get("sequence") if isinstance(data, dict) else None
        if not isinstance(seq, list):
            messagebox.showerror("Import Failed",
                                 "File does not contain a valid sequence.")
            return
        mode = messagebox.askyesnocancel(
            "Import Sequence",
            f"Found {len(seq)} step(s) in '{Path(path).name}'.\n\n"
            "Replace current sequence?\n"
            "  Yes  — replace all existing steps\n"
            "  No   — append to existing steps",
        )
        if mode is None:
            return
        self._push_undo()
        if mode:
            self._cfg.setdefault("macro", {})["sequence"] = seq
        else:
            self._cfg.setdefault("macro", {}).setdefault("sequence", []).extend(seq)
        self._save_cfg_silent()
        self._refresh_macro_list()
        action = "Replaced" if mode else "Appended"
        _log.info("Macro import: %s %d steps from %s", action.lower(), len(seq), path)
        self._lbl_status.config(
            text=f"✓ {action} {len(seq)} step(s) from '{Path(path).name}'", fg=_SUCCESS)
        self.after(4000, lambda: self._lbl_status.config(text="", fg=_TEXT_MID))

    def _add_pos_step(self) -> None:
        """Open a dialog to set coordinates, with optional click-to-capture."""
        dlg = tk.Toplevel(self)
        dlg.title("Add Position Step")
        dlg.configure(bg=_BG_CARD)
        dlg.resizable(False, False)
        self._show_popup(dlg, 320, 268)
        dlg.grab_set()

        tk.Label(dlg, text="ADD POSITION STEP",
                 font=_F_HEAD, fg=_ACCENT, bg=_BG_CARD).pack(pady=(14, 10))

        # X / Y entries
        coords_f = tk.Frame(dlg, bg=_BG_CARD)
        coords_f.pack(padx=20)
        tk.Label(coords_f, text="X:", font=_F_LABEL, fg=_TEXT_MID,
                 bg=_BG_CARD).pack(side="left")
        x_var = tk.StringVar(value="0")
        tk.Entry(coords_f, textvariable=x_var, width=7, font=_F_MONO,
                 bg=_ENTRY_BG, fg=_TEXT, insertbackground=_TEXT,
                 relief="flat", highlightthickness=1,
                 highlightbackground=_BORDER).pack(side="left", ipady=4, padx=(2, 14))
        tk.Label(coords_f, text="Y:", font=_F_LABEL, fg=_TEXT_MID,
                 bg=_BG_CARD).pack(side="left")
        y_var = tk.StringVar(value="0")
        tk.Entry(coords_f, textvariable=y_var, width=7, font=_F_MONO,
                 bg=_ENTRY_BG, fg=_TEXT, insertbackground=_TEXT,
                 relief="flat", highlightthickness=1,
                 highlightbackground=_BORDER).pack(side="left", ipady=4, padx=(2, 0))

        # Click-to-capture
        def _do_capture_pos() -> None:
            dlg.grab_release()   # must release before hiding — grab blocks overlay clicks
            dlg.withdraw()
            self.withdraw()
            self.update_idletasks()
            self.after(250, lambda: _grab_pos())

        def _grab_pos() -> None:
            sw = self.winfo_screenwidth()
            sh = self.winfo_screenheight()
            try:
                shot = ImageGrab.grab()
            except Exception:
                self.deiconify(); dlg.deiconify()
                return

            # Pre-compute client-area origin so on_move can show correct coords
            _client_ox = _client_oy = None
            try:
                wc_pre = WindowController(self._cfg["window_title"])
                if wc_pre.find_window():
                    origin = win32gui.ClientToScreen(wc_pre.hwnd, (0, 0))
                    _client_ox, _client_oy = origin
            except Exception:
                pass

            overlay = tk.Toplevel(self)
            overlay.overrideredirect(True)
            overlay.geometry(f"{sw}x{sh}+0+0")
            overlay.attributes("-topmost", True)
            ph = ImageTk.PhotoImage(shot)
            cv = tk.Canvas(overlay, width=sw, height=sh,
                           highlightthickness=0, cursor="crosshair")
            cv.pack(fill="both", expand=True)
            cv.create_image(0, 0, anchor="nw", image=ph)
            cv.image = ph
            cv.create_rectangle(0, 0, sw, sh,
                                 fill="black", stipple="gray25", outline="")
            cv.create_rectangle(sw // 2 - 215, 10, sw // 2 + 215, 46,
                                 fill="#0D111E", outline="#22D3EE", width=1)
            cv.create_text(sw // 2, 28,
                           text="Click the position you want  ·  ESC to cancel",
                           fill="#E2E8F0", font=("Segoe UI", 12, "bold"))
            coord_tag = cv.create_text(sw // 2, sh - 30, text="",
                                        fill="#22D3EE", font=("Consolas", 11))

            def on_move(e: tk.Event) -> None:
                if _client_ox is not None:
                    gx = e.x - _client_ox
                    gy = e.y - _client_oy
                    cv.itemconfig(coord_tag,
                                  text=f"Game X: {gx}   Game Y: {gy}")
                else:
                    cv.itemconfig(coord_tag, text=f"X: {e.x}   Y: {e.y}")

            def on_click(e: tk.Event) -> None:
                overlay.destroy()
                self.deiconify()
                dlg.deiconify()
                dlg.grab_set()   # restore modal grab
                # e.x / e.y are screen coordinates (overlay is pinned at 0,0).
                # Convert to client-relative so click_global works correctly.
                wc2 = WindowController(self._cfg["window_title"])
                if wc2.find_window():
                    try:
                        cp = win32gui.ScreenToClient(wc2.hwnd, (e.x, e.y))
                        x_var.set(str(cp[0]))
                        y_var.set(str(cp[1]))
                        global_var.set(True)
                        return
                    except Exception:
                        pass
                # Fallback: game window not found — store raw screen coords
                x_var.set(str(e.x))
                y_var.set(str(e.y))
                global_var.set(False)

            def on_esc(_: tk.Event) -> None:
                overlay.destroy()
                self.deiconify()
                dlg.deiconify()
                dlg.grab_set()   # restore modal grab

            cv.bind("<Motion>", on_move)
            cv.bind("<Button-1>", on_click)
            overlay.bind("<Escape>", on_esc)
            overlay.focus_force()

        HoverButton(dlg, text="📷  Click to Capture Position",
                    command=_do_capture_pos,
                    font=_F_SMALL, bg=_BG_HL, fg=_ACCENT,
                    hover_bg=_ACCENT, hover_fg="#000",
                    pady=5).pack(fill="x", padx=20, pady=(8, 4))

        # Global toggle — custom visible label (selectcolor is invisible on dark bg)
        global_var = tk.BooleanVar(value=True)
        _g_row = tk.Frame(dlg, bg=_BG_CARD)
        _g_row.pack(anchor="w", padx=20, pady=(4, 0))
        _g_lbl = tk.Label(_g_row, text="✓", width=2, fg="#10B981", bg="#0B2E1E",
                          font=("Segoe UI", 10, "bold"), relief="solid", bd=1,
                          cursor="hand2", padx=2, pady=0)
        _g_lbl.pack(side="left")
        tk.Label(_g_row, text="  Global (screen coordinates)", font=_F_SMALL,
                 fg=_TEXT, bg=_BG_CARD, cursor="hand2").pack(side="left")

        def _toggle_global(lbl=_g_lbl, var=global_var):
            new_val = not var.get()
            var.set(new_val)
            if new_val:
                lbl.config(text="✓", fg="#10B981", bg="#0B2E1E")
            else:
                lbl.config(text="✗", fg="#64748B", bg="#0D111E")

        _g_lbl.bind("<Button-1>", lambda e: _toggle_global() or "break")
        _g_row.bind("<Button-1>", lambda e: _toggle_global())

        # Inline validation label — hidden until an error occurs
        _err_lbl = tk.Label(dlg, text="", font=_F_SMALL, fg=_DANGER, bg=_BG_CARD)
        _err_lbl.pack(pady=(4, 0))

        def do_add() -> None:
            try:
                x = int(x_var.get())
                y = int(y_var.get())
            except ValueError:
                _err_lbl.config(text="⚠  X and Y must be whole numbers.")
                return
            _err_lbl.config(text="")
            suffix = ",GLOBAL" if global_var.get() else ""
            self._push_undo()
            self._cfg["macro"]["sequence"].append(f"POS:{x},{y}{suffix}")
            self._save_cfg_silent(); self._refresh_macro_list()
            dlg.destroy()

        tk.Frame(dlg, bg=_BORDER_LT, height=1).pack(fill="x", pady=(6, 0))
        btns = tk.Frame(dlg, bg=_BG_CARD)
        btns.pack(fill="x", padx=20, pady=10)
        HoverButton(btns, text="Add Step", command=do_add,
                    font=_F_LABEL, bg=_ACCENT, fg="#000",
                    hover_bg="#00b8cc", hover_fg="#000",
                    pady=6, padx=20).pack(side="right", padx=(4, 0))
        HoverButton(btns, text="Cancel", command=dlg.destroy,
                    font=_F_LABEL, bg=_BG_HL, fg=_TEXT_MID,
                    hover_bg=_BG_CARD, hover_fg=_TEXT,
                    pady=6, padx=12).pack(side="right")
        dlg.bind("<Return>", lambda _: do_add())
        dlg.bind("<Escape>", lambda _: dlg.destroy())
        dlg.protocol("WM_DELETE_WINDOW", dlg.destroy)

    def _add_wait_step(self) -> None:
        self._push_undo()
        self._cfg["macro"]["sequence"].append("WAIT:1.0")
        self._save_cfg_silent(); self._refresh_macro_list()

    def _add_key_step(self) -> None:
        k = simpledialog.askstring("Add Key", "Enter key (e.g. SPACE, ESC, A, 1):")
        if k:
            self._push_undo()
            self._cfg["macro"]["sequence"].append(f"KEY:{k.upper().strip()}")
            self._save_cfg_silent(); self._refresh_macro_list()

    def _open_template_picker(self, on_select, confirm_label: str = "SELECT") -> None:
        """Open a reusable thumbnail picker popup.

        Args:
            on_select: Callable(filename: str) invoked when the user confirms.
            confirm_label: Text for the confirm button.
        """
        files = sorted(
            p for p in (_HERE / "templates").rglob("*.png") if p.is_file()
        )
        if not files:
            on_select(None)
            return

        top = tk.Toplevel(self)
        top.title("Select Template")
        top.configure(bg=_BG_CARD)
        self._show_popup(top, 390, 480)
        top.grab_set()

        tk.Label(top, text="SELECT TEMPLATE",
                 font=_F_HEAD, fg=_ACCENT, bg=_BG_CARD).pack(pady=(12, 6))

        # Search bar
        search_var = tk.StringVar()
        sf = tk.Frame(top, bg=_BG_CARD)
        sf.pack(fill="x", padx=12, pady=(0, 6))
        tk.Label(sf, text="🔍", font=_F_BODY, fg=_TEXT_MID,
                 bg=_BG_CARD).pack(side="left", padx=(0, 4))
        tk.Entry(sf, textvariable=search_var, font=_F_MONO,
                 bg=_ENTRY_BG, fg=_TEXT, insertbackground=_TEXT,
                 relief="flat", highlightthickness=1,
                 highlightbackground=_BORDER
                 ).pack(fill="x", expand=True, ipady=4)

        # Scrollable thumbnail rows
        outer = tk.Frame(top, bg=_BG)
        outer.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        canvas = tk.Canvas(outer, bg=_BG, highlightthickness=0)
        sb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        rows_frame = tk.Frame(canvas, bg=_BG)
        win_id = canvas.create_window((0, 0), window=rows_frame, anchor="nw")
        rows_frame.bind("<Configure>",
                        lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfig(win_id, width=e.width))

        def _scroll(e: tk.Event) -> None:
            canvas.yview_scroll(-1 * (e.delta // 120), "units")
        canvas.bind("<MouseWheel>", _scroll)

        selected: list[str | None] = [None]
        row_widgets: list[tk.Frame] = []

        def _select(filename: str, row: tk.Frame) -> None:
            selected[0] = filename
            for r in row_widgets:
                r.config(bg=_BG)
                for c in r.winfo_children():
                    try: c.config(bg=_BG)
                    except tk.TclError: pass
            row.config(bg=_ACCENT_MU)
            for c in row.winfo_children():
                try: c.config(bg=_ACCENT_MU)
                except tk.TclError: pass

        def _confirm() -> None:
            if selected[0]:
                on_select(selected[0])
                top.destroy()

        def _rebuild(*_) -> None:
            for w in rows_frame.winfo_children():
                w.destroy()
            row_widgets.clear()
            selected[0] = None
            term = search_var.get().lower()
            for path in [p for p in files if term in p.name.lower()]:
                row = tk.Frame(rows_frame, bg=_BG, cursor="hand2")
                row.pack(fill="x", pady=1)
                row_widgets.append(row)
                try:
                    img = Image.open(path)
                    img.thumbnail((48, 36), Image.BILINEAR)
                    ph = ImageTk.PhotoImage(img)
                    lbl_img = tk.Label(row, image=ph, bg=_BG, padx=4)
                    lbl_img.image = ph
                    lbl_img.pack(side="left", pady=4)
                except Exception:
                    tk.Label(row, text="[?]", width=6, bg=_BG,
                             fg=_TEXT_DIM, font=_F_SMALL
                             ).pack(side="left", padx=4, pady=4)
                tk.Label(row, text=path.name, font=_F_MONO, fg=_TEXT,
                         bg=_BG, anchor="w"
                         ).pack(side="left", fill="x", expand=True, padx=4)
                fn = path.name
                for w in (row, *row.winfo_children()):
                    w.bind("<Button-1>",
                           lambda e, f=fn, r=row: _select(f, r))
                    w.bind("<Double-Button-1>",
                           lambda e, f=fn, r=row: (_select(f, r), _confirm()))
                    w.bind("<MouseWheel>", _scroll)

        search_var.trace_add("write", _rebuild)
        _rebuild()
        sf.winfo_children()[-1].focus_set()

        tk.Frame(top, bg=_BORDER_LT, height=1).pack(fill="x")
        btns = tk.Frame(top, bg=_BG_CARD)
        btns.pack(fill="x", padx=12, pady=10)
        HoverButton(btns, text=confirm_label, command=_confirm,
                    font=_F_LABEL, bg=_ACCENT, fg="#000",
                    hover_bg="#00b8cc", hover_fg="#000",
                    pady=6, padx=20).pack(side="right", padx=(4, 0))
        HoverButton(btns, text="Cancel", command=top.destroy,
                    font=_F_LABEL, bg=_BG_HL, fg=_TEXT_MID,
                    hover_bg=_BG_CARD, hover_fg=_TEXT,
                    pady=6, padx=12).pack(side="right")
        top.bind("<Return>", lambda _: _confirm())
        top.bind("<Escape>", lambda _: top.destroy())

    def _add_tpl_step(self) -> None:
        """Open the thumbnail picker to add a template step to the sequence."""
        def _on_select(filename: str | None) -> None:
            self._push_undo()
            name = filename or "template_name.png"
            self._cfg["macro"]["sequence"].append(name)
            self._save_cfg_silent()
            self._refresh_macro_list()
        self._open_template_picker(_on_select, confirm_label="ADD STEP")

    def _macro_record_sub(self, lb):
        wc = WindowController(self._cfg["window_title"])
        if not wc.find_window():
            messagebox.showerror("Error", "Game window not found."); return

        overlay = tk.Toplevel(self)
        overlay.overrideredirect(True); overlay.attributes("-topmost", True)
        overlay.configure(bg=_RED)
        overlay.geometry(f"200x40+{self.winfo_x()+400}+{self.winfo_y()+100}")
        tk.Label(overlay, text="🔴 SUB-REC CLICKS",
                 bg=_RED, fg="#000", font=_F_HEAD).pack(expand=True)
        tk.Label(overlay, text="Press ESC to Stop",
                 bg=_RED, fg="#000", font=_F_SMALL).pack()
        self.update()

        recorded = []; last_t = [time.monotonic()]

        def on_click(x, y, button, pressed):
            if pressed and button == pynput_mouse.Button.left:
                now = time.monotonic(); diff = now - last_t[0]; last_t[0] = now
                if recorded: recorded.append(f"WAIT:{round(diff, 2)}")
                try:
                    cp = win32gui.ScreenToClient(wc.hwnd, (int(x), int(y)))
                    if 0 <= cp[0] <= wc.client_width and 0 <= cp[1] <= wc.client_height:
                        recorded.append(f"POS:{cp[0]},{cp[1]},GLOBAL")
                except (OSError, ValueError):
                    pass

        def on_press(key):
            if key == pynput_kb.Key.esc: return False

        ml = pynput_mouse.Listener(on_click=on_click)
        kl = pynput_kb.Listener(on_press=on_press)

        def _run():
            ml.start(); kl.start(); kl.join(); ml.stop()
            self.after(0, overlay.destroy)
            if recorded:
                self.after(0, lambda: [lb.insert("end", r) for r in recorded])

        threading.Thread(target=_run, daemon=True).start()

    def _macro_record(self):
        wc = WindowController(self._cfg["window_title"])
        if not wc.find_window():
            messagebox.showerror("Error", "Game window not found. Please target it in SETTINGS."); return

        mode = messagebox.askyesnocancel("Recording", "Do you want to CLEAR the current sequence before recording?")
        if mode is None: return

        if mode:
            self._push_undo()   # allow Ctrl+Z to restore the cleared sequence
            self._cfg["macro"]["sequence"] = []
            self._refresh_macro_list()

        overlay = tk.Toplevel(self)
        overlay.overrideredirect(True)
        overlay.attributes("-topmost", True)
        overlay.geometry(f"250x60+{self.winfo_x()+350}+{self.winfo_y()+50}")
        overlay.configure(bg=_RED)
        tk.Label(overlay, text="🔴 RECORDING CLICKS...",
                 bg=_RED, fg="#000", font=_F_TITLE).pack(expand=True)
        tk.Label(overlay, text="Press ESC to Stop",
                 bg=_RED, fg="#000", font=_F_SMALL).pack()
        self.update()

        recorded_steps = []
        last_click_time = [time.monotonic()]

        def on_click(x, y, button, pressed):
            if pressed and button == pynput_mouse.Button.left:
                now = time.monotonic()
                diff = now - last_click_time[0]; last_click_time[0] = now
                if len(recorded_steps) > 0:
                    recorded_steps.append(f"WAIT:{round(diff, 2)}")
                try:
                    cp = win32gui.ScreenToClient(wc.hwnd, (int(x), int(y)))
                    if 0 <= cp[0] <= wc.client_width and 0 <= cp[1] <= wc.client_height:
                        recorded_steps.append(f"POS:{cp[0]},{cp[1]},GLOBAL")
                except (OSError, ValueError):
                    pass

        def on_press(key):
            if key == pynput_kb.Key.esc: return False

        mouse_listener = pynput_mouse.Listener(on_click=on_click)
        kb_listener    = pynput_kb.Listener(on_press=on_press)

        def _run_recording():
            mouse_listener.start(); kb_listener.start()
            kb_listener.join(); mouse_listener.stop()
            self.after(0, overlay.destroy)
            if recorded_steps:
                self.after(0, lambda: self._finalize_recording(recorded_steps))

        threading.Thread(target=_run_recording, daemon=True).start()

    def _finalize_recording(self, steps):
        self._push_undo()   # make the entire recording reversible with Ctrl+Z
        for s in steps:
            self._cfg["macro"]["sequence"].append(s)
        self._refresh_macro_list(); self._save_cfg_silent()
        self._lbl_status.config(
            text=f"✓ Recording finished — added {len(steps)} step{'s' if len(steps) != 1 else ''}",
            fg=_SUCCESS)
        self.after(4000, lambda: self._lbl_status.config(text="", fg=_TEXT_MID))

    def _open_capture(self, full_screen=False):
        """Capture a new template and add it to the sequence. Uses the full-screen overlay."""
        def _on_saved(stem: str) -> None:
            fname = f"{stem}.png"
            self._cfg["macro"]["sequence"].append(fname)
            self._save_cfg_silent()
            self._refresh_macro_list()
        self._template_capture(on_saved=_on_saved)

    # ── Profiles ──────────────────────────────────────────────────────────────
    def _refresh_profiles(self):
        self._profile_list.delete(0, "end")
        for p in list_profiles(str(_HERE)): self._profile_list.insert("end", p)

    def _profile_load(self) -> None:
        """Load the selected profile, apply it immediately, and activate the SAVE button."""
        idx = self._profile_list.curselection()
        if not idx:
            return
        name = self._profile_list.get(idx[0])
        try:
            self._cfg = apply_path_resolution(load_profile(name, str(_HERE)))
        except (FileNotFoundError, Exception) as exc:
            messagebox.showerror("Load Profile", f"Could not load '{name}':\n{exc}")
            return
        configure_logging_from_config(self._cfg.get("logging", {}))
        self._invalidate_template_cache()
        # Clear undo/redo stacks — they belong to the previous profile's sequence
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._update_undo_btns()
        self._refresh_macro_list()
        self._refresh_cfg_panel()
        for tag, reset in (("loops", "0"), ("avg_loop", "—"), ("last_loop", "—"), ("cycle_ms", "—")):
            if tag in self._stat_vals:
                self._stat_vals[tag].config(text=reset)
        # Track active profile and enable the SAVE button
        self._active_profile = name
        if self._lbl_active_prof:
            self._lbl_active_prof.config(text=name, fg=_ACCENT)
        if self._btn_profile_save:
            self._btn_profile_save.set_style(_BG_HL, _SUCCESS)
            self._btn_profile_save.config(state="normal")

    def _profile_save(self) -> None:
        """Overwrite the currently active profile file with the current config."""
        if not self._active_profile:
            return
        path = _HERE / "profiles" / self._active_profile
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._cfg, f, indent=2)
            _log.info("Profile saved: %s", self._active_profile)
            # Brief flash on the active label to confirm
            if self._lbl_active_prof:
                self._lbl_active_prof.config(text=f"✓ Saved — {self._active_profile}", fg=_SUCCESS)
                self.after(2500, lambda: self._lbl_active_prof.config(
                    text=self._active_profile or "— none —",
                    fg=_ACCENT if self._active_profile else _TEXT_MID,
                ))
        except OSError as e:
            _log.error("Failed to save profile '%s': %s", self._active_profile, e)
            messagebox.showerror("Save Failed",
                                 f"Could not save profile '{self._active_profile}':\n{e}")

    def _profile_save_as(self) -> None:
        """Save the current config as a new profile, prompting for a name."""
        n = simpledialog.askstring("Save Profile As", "Enter a name for the new profile:")
        if not n:
            return
        filename = n if n.endswith(".json") else n + ".json"
        path = _HERE / "profiles" / filename
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._cfg, f, indent=2)
            self._refresh_profiles()
            # Auto-select and activate the newly saved profile
            items = list(self._profile_list.get(0, "end"))
            if filename in items:
                self._profile_list.selection_clear(0, "end")
                self._profile_list.selection_set(items.index(filename))
            self._active_profile = filename
            if self._lbl_active_prof:
                self._lbl_active_prof.config(text=filename, fg=_ACCENT)
            if self._btn_profile_save:
                self._btn_profile_save.set_style(_BG_HL, _SUCCESS)
                self._btn_profile_save.config(state="normal")
            _log.info("Profile saved as: %s", filename)
        except OSError as e:
            _log.error("Failed to save profile '%s': %s", filename, e)
            messagebox.showerror("Save Failed", f"Could not save profile '{filename}':\n{e}")

    def _profile_del(self) -> None:
        """Delete the selected profile file and clear the active-profile state if it matches."""
        idx = self._profile_list.curselection()
        if not idx:
            return
        name = self._profile_list.get(idx[0])
        try:
            ((_HERE / "profiles" / name)).unlink()
            if self._active_profile == name:
                self._active_profile = None
                if self._lbl_active_prof:
                    self._lbl_active_prof.config(text="— none —", fg=_TEXT_MID)
                if self._btn_profile_save:
                    self._btn_profile_save.set_style(_BG_HL, _TEXT_DIM)
                    self._btn_profile_save.config(state="disabled")
            self._refresh_profiles()
        except OSError as e:
            _log.error("Failed to delete profile '%s': %s", name, e)
            messagebox.showerror("Delete Failed", f"Could not delete '{name}':\n{e}")

    def _profile_duplicate(self) -> None:
        """Copy the selected profile to a new filename without changing the active profile."""
        idx = self._profile_list.curselection()
        if not idx:
            return
        src_name = self._profile_list.get(idx[0])
        src_path = _HERE / "profiles" / src_name
        new_name = simpledialog.askstring(
            "Duplicate Profile",
            f"New name for the copy of '{src_name}':",
            initialvalue=src_name.replace(".json", "") + "_copy",
        )
        if not new_name:
            return
        if not new_name.endswith(".json"):
            new_name += ".json"
        dst_path = _HERE / "profiles" / new_name
        if dst_path.exists():
            messagebox.showerror("Duplicate Failed",
                                 f"'{new_name}' already exists.")
            return
        try:
            shutil.copy2(src_path, dst_path)
            self._refresh_profiles()
            self._lbl_status.config(
                text=f"✓ Duplicated as '{new_name}'", fg=_SUCCESS)
            self.after(4000, lambda: self._lbl_status.config(text="", fg=_TEXT_MID))
        except OSError as e:
            _log.error("Failed to duplicate profile '%s': %s", src_name, e)
            messagebox.showerror("Duplicate Failed", str(e))

    # ── Polling ───────────────────────────────────────────────────────────────
    def _poll_frame(self) -> None:
        """Main-thread timer: pull the latest frame from queue and display on the live feed canvas."""
        try:
            f = self._frame_queue.get_nowait()
            rgb = cv2.cvtColor(f, cv2.COLOR_BGR2RGB)
            pil = Image.fromarray(rgb)
            if self._feed_canvas:
                cw = self._feed_canvas.winfo_width()
                ch = self._feed_canvas.winfo_height()
                if cw > 50:
                    pil.thumbnail((cw, ch), Image.BILINEAR)
                    self._tk_img2 = ImageTk.PhotoImage(pil)
                    self._feed_canvas.delete("all")
                    self._feed_canvas.create_image(cw // 2, ch // 2, image=self._tk_img2)
                    # ── Step overlay strip ────────────────────────────────────
                    if self._is_running:
                        step_idx = self._active_step_ref[0]
                        step_lbl = ""
                        if step_idx >= 0:
                            seq = self._cfg.get("macro", {}).get("sequence", [])
                            if step_idx < len(seq):
                                it  = seq[step_idx]
                                raw = it if isinstance(it, str) else it.get("template", "")
                                lbl = "" if isinstance(it, str) else (it.get("label", "") or "")
                                step_lbl = lbl or raw
                                if len(step_lbl) > 32:
                                    step_lbl = step_lbl[:30] + "…"
                        loops = self._last_stats.get("loops", 0)
                        ov_line1 = f"▶  Step {step_idx + 1}  ·  {step_lbl}" if step_idx >= 0 else "▶  Running…"
                        ov_line2 = f"Loops: {loops}"
                        bar_h = 36
                        self._feed_canvas.create_rectangle(
                            0, ch - bar_h, cw, ch,
                            fill=_BG_INSET, outline="", stipple="gray50")
                        self._feed_canvas.create_text(
                            8, ch - bar_h + 6,
                            text=ov_line1, anchor="nw",
                            fill=_ACCENT, font=("Segoe UI", 8, "bold"))
                        self._feed_canvas.create_text(
                            8, ch - bar_h + 20,
                            text=ov_line2, anchor="nw",
                            fill=_TEXT_MID, font=("Segoe UI", 7))
        except queue.Empty:
            pass
        self.after(50, self._poll_frame)

    def _poll_log(self) -> None:
        """Main-thread timer: tail the log file and append new lines to the log panel."""
        log_f = Path(self._cfg.get("logging", {}).get("log_file", str(_HERE / "bot.log")))
        if log_f.exists():
            # Bug fix: detect log rotation — if file shrank, reset position to 0
            try:
                file_size = log_f.stat().st_size
                if self._log_pos > file_size:
                    self._log_pos = 0
            except OSError:
                self.after(200, self._poll_log)
                return

            with log_f.open("r", encoding="utf-8", errors="replace") as f:
                f.seek(self._log_pos)
                txt = f.read()
                self._log_pos = f.tell()
                if txt:
                    at_bottom = self._log_box.yview()[1] >= 0.98
                    for line in txt.splitlines(keepends=True):
                        ul = line.upper()
                        if "ERROR" in ul:
                            tag = "ERROR"
                        elif "WARNING" in ul:
                            tag = "WARNING"
                        elif "DEBUG" in ul:
                            tag = "DEBUG"
                        else:
                            tag = "INFO"
                        self._log_box.insert("end", line, tag)
                    if at_bottom:
                        self._log_box.see("end")
                    # Validate cycle-time value before displaying
                    if "Cycle:" in txt:
                        for seg in txt.split("Cycle:")[1:]:
                            ms_str = seg.split("ms")[0].strip()
                            if ms_str.lstrip("-").isdigit():
                                self._lbl_cycle.config(text=f"Cycle: {ms_str} ms")
                                self._sb_cycle.config(text=f"Cycle: {ms_str} ms")
                                if "cycle_ms" in self._stat_vals:
                                    self._stat_vals["cycle_ms"].config(text=ms_str)
                                break

                    # Detect stop-reason keywords so _stop_bot can display them
                    txt_u = txt.upper()
                    if "WINDOW TITLE CHANGED" in txt_u:
                        self._stop_reason = "Window title changed"
                    elif "MAX RUNTIME" in txt_u and "REACHED" in txt_u:
                        self._stop_reason = "Max runtime reached"
        self.after(200, self._poll_log)

    def _set_log_filter(self, level: str) -> None:
        """Show/hide log lines by level using tag foreground color.
        Only applies tag_config when the filter actually changes to avoid
        redundant work on every poll tick."""
        if level == self._log_filter and hasattr(self, "_log_filter_applied"):
            return  # Nothing changed — skip the 4× tag_config calls
        lvl_map = {
            "ALL": {"DEBUG": _LOG_DEBUG, "INFO": _LOG_INFO, "WARNING": _LOG_WARNING, "ERROR": _LOG_ERROR},
            "DBG": {"DEBUG": _LOG_DEBUG, "INFO": _BG_INSET,  "WARNING": _BG_INSET,   "ERROR": _BG_INSET},
            "INF": {"DEBUG": _BG_INSET,  "INFO": _LOG_INFO,  "WARNING": _BG_INSET,   "ERROR": _BG_INSET},
            "WRN": {"DEBUG": _BG_INSET,  "INFO": _BG_INSET,  "WARNING": _LOG_WARNING, "ERROR": _BG_INSET},
            "ERR": {"DEBUG": _BG_INSET,  "INFO": _BG_INSET,  "WARNING": _BG_INSET,   "ERROR": _LOG_ERROR},
        }
        self._log_filter = level
        self._log_filter_applied = True
        colors = lvl_map.get(level, lvl_map["ALL"])
        for tag, fg in colors.items():
            self._log_box.tag_config(tag, foreground=fg)

    def _poll_stats(self) -> None:
        """Drain the stats queue and update the live dashboard cards."""
        try:
            data = self._stats_queue.get_nowait()
            self._last_stats = data   # persist for session history
            if "loops" in self._stat_vals:
                done  = data.get("loops", 0)
                total = int(self._cfg.get("macro", {}).get("repeat_count", 0) or 0)
                if total > 0:
                    pct = done / total
                    color = _DANGER if pct >= 1.0 else (_WARN if pct >= 0.8 else _ACCENT)
                    self._stat_vals["loops"].config(
                        text=f"{done} / {total}", fg=color)
                else:
                    self._stat_vals["loops"].config(
                        text=str(done), fg=_ACCENT)
            if "avg_loop" in self._stat_vals:
                avg_s = data.get("avg_ms", 0) / 1000.0
                self._stat_vals["avg_loop"].config(text=f"{avg_s:.1f}")
            if "last_loop" in self._stat_vals:
                last_s = data.get("loop_ms", 0) / 1000.0
                self._stat_vals["last_loop"].config(text=f"{last_s:.1f}")
        except queue.Empty:
            pass # tick session timer while bot is running

        # ── Highlight the currently-executing step row ────────────────────────
        active_idx = self._active_step_ref[0]
        for j, bar in enumerate(self._step_active_bars):
            try:
                bar.config(
                    bg=_ACCENT if (self._is_running and j == active_idx)
                    else bar._normal_bg)   # type: ignore[attr-defined]
            except Exception:
                pass

        if self._is_running and self._session_start > 0:
            elapsed = int(time.monotonic() - self._session_start)
            mm, ss = divmod(elapsed, 60)
            hh, mm = divmod(mm, 60)
            txt = f"{hh:02d}:{mm:02d}:{ss:02d}" if hh else f"{mm:02d}:{ss:02d}"
            if "session" in self._stat_vals:
                self._stat_vals["session"].config(text=txt)

        # ── Remote dashboard sync ─────────────────────────────────────────────
        if self._remote_dash is not None:
            # Push current stats to the HTTP server's shared dict
            d         = self._last_stats
            loops_val = d.get("loops", None)
            avg_ms    = d.get("avg_ms", None)
            loop_ms   = d.get("loop_ms", None)
            session_s = (int(time.monotonic() - self._session_start)
                         if self._is_running and self._session_start > 0 else None)
            # Format session as mm:ss / h:mm
            if session_s is not None:
                _m, _s = divmod(session_s, 60)
                _h, _m = divmod(_m, 60)
                sess_txt = f"{_h:02d}:{_m:02d}:{_s:02d}" if _h else f"{_m:02d}:{_s:02d}"
            else:
                sess_txt = None
            # Current step label
            seq  = self._cfg.get("macro", {}).get("sequence", [])
            sidx = self._active_step_ref[0]
            if self._is_running and 0 <= sidx < len(seq):
                item = seq[sidx]
                step_txt = (item if isinstance(item, str)
                            else (item.get("label") or item.get("template") or f"step {sidx + 1}"))
            else:
                step_txt = None
            self._remote_dash.update_stats(
                running = self._is_running,
                paused  = self._pause_event.is_set(),
                loops   = loops_val,
                session = sess_txt,
                avg_s   = f"{avg_ms / 1000.0:.1f}" if avg_ms is not None else None,
                last_s  = f"{loop_ms / 1000.0:.1f}" if loop_ms is not None else None,
                step    = step_txt,
            )
            # Drain remote commands and dispatch them on the main thread
            for cmd in self._remote_dash.drain_commands():
                if cmd == "start" and not self._is_running:
                    self._start_bot()
                elif cmd == "stop" and self._is_running:
                    self._stop_event.set()
                    self.after(0, self._stop_bot)
                elif cmd == "pause" and self._is_running:
                    self._toggle_pause()

        self.after(400, self._poll_stats)

    # ── Template cache ────────────────────────────────────────────────────────
    def _collect_template_stems(self) -> set[str]:
        """Cached recursive scan of the configured template directory for .png/.bmp stems.

        Respects vision.template_dir from config (falls back to templates/).
        Cache is invalidated by _invalidate_template_cache() when templates change.
        """
        if self._template_stems_cache is None:
            cfg_dir = self._cfg.get("vision", {}).get("template_dir", "templates")
            tpl_dir = Path(cfg_dir) if Path(cfg_dir).is_absolute() else _HERE / cfg_dir
            stems: set[str] = set()
            if tpl_dir.exists():
                for pattern in ("*.png", "*.bmp"):
                    for p in tpl_dir.rglob(pattern):
                        stems.add(p.stem.lower())
            self._template_stems_cache = stems
        return self._template_stems_cache

    def _invalidate_template_cache(self) -> None:
        self._template_stems_cache = None

    @staticmethod
    def _normalize_template_stem(name: str | None) -> str | None:
        if not name or not str(name).strip(): return None
        return Path(str(name).strip()).stem.lower()

    # ── Macro test step ───────────────────────────────────────────────────────
    def _macro_test_step(self, step_idx: int = -1) -> None:
        if step_idx < 0:
            return
        seq = self._cfg.get("macro", {}).get("sequence", [])
        if step_idx >= len(seq):
            return   # step was deleted between row build and click
        i    = step_idx
        item = seq[i]
        raw  = item if isinstance(item, str) else (item.get("template") or "")

        # Guard both plain-string AND dict-format command steps
        if str(raw).upper().strip().startswith(("POS:", "WAIT:", "KEY:")):
            messagebox.showinfo("Test step", "This is a direct command (POS/WAIT/KEY), not a template search.")
            return

        stem = self._normalize_template_stem(raw)
        if not stem:
            messagebox.showerror("Test step", "No template name for this step."); return

        wc = WindowController(self._cfg["window_title"])
        if not wc.find_window():
            messagebox.showerror("Test step", "Game window not found."); return

        vcfg    = self._cfg.get("vision", {})
        tpl_dir = vcfg.get("template_dir", str(_HERE / "templates"))
        ir = ImageRecognizer(
            template_dir=str(tpl_dir),
            threshold=float(vcfg.get("match_threshold", 0.5)),
            use_grayscale=bool(vcfg.get("use_grayscale", True)),
            templates_meta_file=vcfg.get("templates_meta_file"))
        frame = wc.capture()
        if frame is None:
            messagebox.showerror("Test step", "Capture failed."); return

        vis = frame.copy()
        hit = ir.find(vis, stem)
        if hit:
            _, conf, bbox = hit
            x, y, w, h = bbox
            cv2.rectangle(vis, (x, y), (x+w, y+h), (0, 255, 0), 2)
            cv2.putText(vis, f"{stem} {conf:.2f}", (x, max(22, y-4)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 1, cv2.LINE_AA)
            msg = f"Match OK — confidence {conf:.3f}"
        else:
            msg = "No match (check template image and threshold)."

        out_dir = _HERE / "debug"; out_dir.mkdir(exist_ok=True)
        out_path = out_dir / "macro_test_preview.png"
        cv2.imwrite(str(out_path), vis)
        messagebox.showinfo("Test step (dry-run)", f"{msg}\n\nSaved: {out_path}")

    # ── Bot control ───────────────────────────────────────────────────────────
    def _start_bot(self) -> None:
        """Validate config, find the game window, then launch BotEngine in a daemon thread."""
        if self._is_running:   # Guard against double-start
            return

        seq = self._cfg.get("macro", {}).get("sequence", [])
        _tpl_dir_cfg = self._cfg.get("vision", {}).get("template_dir", "templates")
        tpl_dir = Path(_tpl_dir_cfg) if Path(_tpl_dir_cfg).is_absolute() else _HERE / _tpl_dir_cfg
        _CMD_PREFIXES = ("POS:", "WAIT:", "KEY:")

        def _is_command_step(s) -> bool:
            """Return True if step is a direct command (POS/WAIT/KEY), not a template lookup."""
            if isinstance(s, str):
                return s.upper().strip().startswith(_CMD_PREFIXES)
            if isinstance(s, dict):
                tpl = (s.get("template") or "").upper().strip()
                return tpl.startswith(_CMD_PREFIXES)
            return False

        # Pre-flight 1: require at least one template on disk when sequence uses them.
        # Skip this guard if every step is a direct command or the sequence is empty.
        has_tpl_steps = any(not _is_command_step(s) for s in seq)
        if has_tpl_steps:
            template_count = (len(list(tpl_dir.rglob("*.png"))) +
                              len(list(tpl_dir.rglob("*.bmp"))))
            if template_count == 0:
                messagebox.showerror(
                    "No Templates",
                    "No template images found in the templates folder.\n"
                    "Go to the Templates tab and click CAPTURE to add some.")
                return

        # Pre-flight 2: warn about any template-based steps whose file is missing.
        # Uses the cached recursive stem scan so subdirectories are covered.
        missing: list[str] = []
        available_stems = self._collect_template_stems()
        for item in seq:
            if _is_command_step(item):
                continue
            candidates: list[str] = []
            if isinstance(item, str):
                candidates.append(item.strip())
            elif isinstance(item, dict):
                for field in ("template", "if_visible"):
                    val = (item.get(field) or "").strip()
                    if val and not val.upper().startswith(_CMD_PREFIXES):
                        candidates.append(val)
            for name in candidates:
                stem = self._normalize_template_stem(name)
                if stem and stem not in available_stems:
                    missing.append(stem)
        if missing:
            lines = "\n".join(f"  • {m}" for m in missing[:10])
            suffix = f"\n  … and {len(missing)-10} more" if len(missing) > 10 else ""
            if not messagebox.askyesno(
                "Missing Templates",
                f"These templates are not found in the templates folder:\n\n"
                f"{lines}{suffix}\n\n"
                f"Steps referencing them will time-out at runtime.\n\nStart anyway?",
                icon="warning",
            ):
                return

        self._is_running = True
        self._stop_event.clear()
        self._stop_after_loop_event.clear()
        self._pause_event.clear()
        self._session_start = time.monotonic()   # start session clock
        # Cancel any stale "→ IDLE" timer left over from the previous stop
        if self._status_clear_job is not None:
            self.after_cancel(self._status_clear_job)
            self._status_clear_job = None

        # Reset live stat cards
        for tag, reset in (("loops", "0"), ("avg_loop", "—"), ("last_loop", "—"), ("session", "00:00")):
            if tag in self._stat_vals:
                self._stat_vals[tag].config(text=reset)
        while not self._stats_queue.empty():
            try: self._stats_queue.get_nowait()
            except queue.Empty: break

        # Find window before flipping any UI state
        wc = WindowController(self._cfg["window_title"])
        if not wc.find_window():
            self._is_running = False
            title = self._cfg.get("window_title", "(not set)")
            messagebox.showerror(
                "Window Not Found",
                f"Could not find a window with title containing:\n\n"
                f"  \"{title}\"\n\n"
                f"Make sure the game is open and the Window Title in Settings matches.")
            return

        self._btn_main_start.config(state="disabled", text="⚡  ACTIVE")
        self._btn_main_stop.config(state="normal")
        # Disable sequence-mutating controls while the engine is running
        for _b in (self._btn_macro_clear, self._btn_macro_import):
            if _b:
                _b.config(state="disabled")
        if self._btn_pause:
            self._btn_pause.config(state="normal", text="⏸  PAUSE", fg=_WARN)
        self._lbl_status.config(text="● RUNNING", fg=_SUCCESS)
        self.title("ChainEX — ▶ Running")
        # Start the status-dot pulse animation
        if self._pulse_job is None:
            self._pulse_job = self.after(600, self._pulse_status)
        threading.Thread(target=self._run_engine, args=(wc,), daemon=True).start()

    def _stop_bot(self) -> None:
        """Signal the engine to stop, reset all UI controls to idle state."""
        # Capture session data before resetting start time
        duration_s = (time.monotonic() - self._session_start) if self._session_start > 0 else 0.0
        had_session = self._session_start > 0

        self._stop_event.set()
        self._pause_event.clear()
        self._is_running = False
        self._active_step_ref[0] = -1   # clear step highlight
        self._secondary_pending = False
        self._secondary_one_shot = False
        self._stop_after_loop_event.clear()
        self._session_start = 0.0
        self._btn_main_start.config(state="normal", text="▶  START BOT")
        self._btn_main_stop.config(state="disabled")
        # Re-enable sequence controls
        for _b in (self._btn_macro_clear, self._btn_macro_import):
            if _b:
                _b.config(state="normal")
        if self._btn_pause:
            self._btn_pause.config(state="disabled", text="⏸  PAUSE")
        # Stop the status-dot pulse animation
        if self._pulse_job is not None:
            self.after_cancel(self._pulse_job)
            self._pulse_job = None
        # Show stop reason briefly, then fall back to IDLE.
        # Cancel any previous timed reset that might still be pending.
        if self._status_clear_job is not None:
            self.after_cancel(self._status_clear_job)
            self._status_clear_job = None
        reason = self._stop_reason
        self._stop_reason = ""
        self.title("ChainEX")
        if reason:
            self._lbl_status.config(text=f"⚠  {reason}", fg=_WARN)
            self._status_clear_job = self.after(
                4000,
                lambda: (
                    self._lbl_status.config(text="● IDLE", fg=_TEXT_MID)
                    if not self._is_running else None
                ),
            )
        else:
            self._lbl_status.config(text="● IDLE", fg=_TEXT_MID)
        # Write session record (only if bot actually ran)
        if had_session:
            self._append_session_record(duration_s, reason or "Manual stop")

        # Rebuild step rows now that _is_running is False — restores filter state,
        # re-enables drag handles, and removes any stale highlight bars.
        self._refresh_macro_list()

    def _toggle_pause(self) -> None:
        """Toggle the engine pause event and update the Pause button label."""
        if not self._is_running:
            return
        if self._pause_event.is_set():
            # ── Resuming ─────────────────────────────────────────────────────
            self._pause_event.clear()
            if self._btn_pause:
                self._btn_pause.config(text="⏸  PAUSE", fg=_WARN)
            self._lbl_status.config(text="● RUNNING", fg=_SUCCESS)
            self.title("ChainEX — ▶ Running")
            # Restart the pulse animation now that we're running again
            if self._pulse_job is None:
                self._pulse_job = self.after(600, self._pulse_status)
        else:
            # ── Pausing ──────────────────────────────────────────────────────
            # Cancel the pulse so the dot stays solid yellow while paused
            if self._pulse_job is not None:
                self.after_cancel(self._pulse_job)
                self._pulse_job = None
            self._pause_event.set()
            if self._btn_pause:
                self._btn_pause.config(text="▶  RESUME", fg=_SUCCESS)
            self._lbl_status.config(text="⏸  PAUSED", fg=_WARN)
            self.title("ChainEX — ⏸ Paused")

    def _run_engine(self, wc: "WindowController") -> None:
        """Daemon-thread target: instantiate BotEngine, run it, then call _stop_bot on the main thread."""
        try:
            # Snapshot config at engine start — UI edits during a run don't affect it
            engine_cfg = copy.deepcopy(self._cfg)
            BotEngine(wc, engine_cfg, self._stop_event, self._frame_queue,
                      stop_after_loop=self._stop_after_loop_event,
                      stats_queue=self._stats_queue,
                      pause_event=self._pause_event,
                      step_ref=self._active_step_ref).run()
        finally:
            if self._secondary_pending:
                self.after(0, self._switch_to_secondary)
            elif self._secondary_one_shot:
                # Run-once secondary finished — restore primary
                self.after(0, self._restore_primary)
            elif self._stop_event.is_set():
                # User pressed stop
                self.after(0, self._stop_bot)
            else:
                # Unexpected exit (window died / error) → try to reconnect
                self.after(0, self._attempt_reconnect)

    def _trigger_secondary_switch(self) -> None:
        """Hotkey handler: request a profile switch after the current loop ends."""
        if not self._is_running:
            return
        sec  = self._cfg.get("secondary_macro", {})
        prof = str(sec.get("profile", "")).strip()
        if not prof:
            self._lbl_status.config(text="⚠  No secondary profile set", fg=_WARN)
            self.after(2500, lambda: self._lbl_status.config(
                text="● RUNNING" if self._is_running else "● IDLE",
                fg=_SUCCESS if self._is_running else _TEXT_MID))
            return
        if self._secondary_pending:
            return   # already armed — ignore repeated presses
        run_once = bool(sec.get("run_once", False))
        if run_once:
            self._primary_cfg_backup = copy.deepcopy(self._cfg)
        self._secondary_pending  = True
        self._secondary_one_shot = run_once
        self._secondary_profile  = prof
        self._stop_after_loop_event.set()
        tag = " (once)" if run_once else ""
        self._lbl_status.config(text=f"⏳ SWITCH{tag} → {Path(prof).stem}", fg=_WARN)

    def _switch_to_secondary(self) -> None:
        """Called on the main thread when the loop-end switch fires."""
        prof = self._secondary_profile
        self._secondary_pending = False
        self._secondary_profile = ""
        self._stop_after_loop_event.clear()

        self._lbl_status.config(text="↻ SWITCHING…", fg=_ACCENT)
        self.update_idletasks()

        one_shot = self._secondary_one_shot
        self._secondary_one_shot = False

        if prof:
            try:
                self._cfg = apply_path_resolution(load_profile(prof, str(_HERE)))
                if one_shot:
                    # Force single-pass so _restore_primary is triggered on finish
                    self._cfg.setdefault("macro", {})["repeat"] = False
                configure_logging_from_config(self._cfg.get("logging", {}))
                self._invalidate_template_cache()
                self._refresh_macro_list()
                self._refresh_cfg_panel()
            except Exception as exc:
                messagebox.showerror("Secondary Macro",
                                     f"Could not load profile '{prof}':\n{exc}")
                self._stop_bot()
                return

        # Re-arm the one-shot flag so _run_engine finally-block can see it
        self._secondary_one_shot = one_shot

        # Reset running state and relaunch engine with the new config
        self._is_running = False
        self._stop_event.clear()
        self._start_bot()

    def _restore_primary(self) -> None:
        """Called after a run-once secondary macro finishes — reload the primary config."""
        self._secondary_one_shot = False
        if self._primary_cfg_backup:
            self._cfg = self._primary_cfg_backup
            self._primary_cfg_backup = None
            configure_logging_from_config(self._cfg.get("logging", {}))
            self._refresh_macro_list()
            self._refresh_cfg_panel()
        self._lbl_status.config(text="↻ PRIMARY RESTORED", fg=_ACCENT)
        self._is_running = False
        self._stop_event.clear()
        self._start_bot()

    def _attempt_reconnect(self) -> None:
        """Window disappeared unexpectedly — wait up to reconnect_timeout_s for it to return."""
        timeout_s = float(self._cfg.get("timing", {}).get("reconnect_timeout_s", 0))
        if timeout_s <= 0 or not self._is_running:
            self._stop_bot()
            return
        self._reconnect_deadline = time.monotonic() + timeout_s
        self._lbl_status.config(text=f"↻ RECONNECTING… ({int(timeout_s)}s)", fg=_WARN)
        self._reconnect_poll()

    def _reconnect_poll(self) -> None:
        """Periodic main-thread callback: recheck for the game window; restart or give up."""
        if not self._is_running or self._stop_event.is_set():
            self._stop_bot()
            return
        remaining = int(self._reconnect_deadline - time.monotonic())
        if remaining <= 0:
            self._lbl_status.config(text="⚠  Reconnect timed out", fg=_DANGER)
            self._stop_bot()
            return
        wc = WindowController(self._cfg["window_title"])
        if wc.find_window():
            self._lbl_status.config(text="● RUNNING", fg=_SUCCESS)
            self._stop_event.clear()
            threading.Thread(target=self._run_engine, args=(wc,), daemon=True).start()
            return
        self._lbl_status.config(text=f"↻ RECONNECTING… ({remaining}s)", fg=_WARN)
        self.after(3000, self._reconnect_poll)

    # ── Session History ───────────────────────────────────────────────────────
    def _append_session_record(self, duration_s: float, stop_reason: str) -> None:
        """Append one session record to session_history.json."""
        stats = self._last_stats
        record = {
            "timestamp":  datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "profile":    self._active_profile or "config.json",
            "duration_s": round(duration_s, 1),
            "loops":      stats.get("loops", 0),
            "avg_loop_s": round(stats.get("avg_ms", 0) / 1000.0, 2),
            "stop_reason": stop_reason,
        }
        try:
            existing: list = []
            if _HISTORY_FILE.exists():
                with open(_HISTORY_FILE, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            existing.append(record)
            # Keep last 200 sessions
            if len(existing) > 200:
                existing = existing[-200:]
            with open(_HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(existing, f, indent=2)
        except (OSError, json.JSONDecodeError) as e:
            _log.warning("Could not write session history: %s", e)
        self._last_stats = {}

    def _build_history_tab(self) -> None:
        """Build the History tab with a scrollable table of past sessions."""
        header = tk.Frame(self._tab_history, bg=_BG_CARD)
        header.pack(fill="x", padx=14, pady=(14, 0))
        tk.Label(header, text="SESSION HISTORY",
                 font=_F_HEAD, fg=_ACCENT, bg=_BG_CARD).pack(side="left", padx=8, pady=8)
        HoverButton(header, text="🗑  CLEAR ALL", command=self._clear_history,
                    font=_F_SMALL, bg=_BG_HL, fg=_DANGER,
                    hover_bg=_DANGER, hover_fg="#000",
                    pady=4, padx=10).pack(side="right", padx=8)

        # ── Duration bar chart ────────────────────────────────────────────────
        chart_card = tk.Frame(self._tab_history, bg=_BG_CARD)
        chart_card.pack(fill="x", padx=14, pady=(6, 0))
        tk.Label(chart_card, text="  SESSION DURATIONS  (last 30)",
                 font=_F_LABEL, fg=_TEXT_DIM, bg=_BG_CARD
                 ).pack(anchor="w", padx=8, pady=(6, 0))
        self._hist_chart_canvas = tk.Canvas(
            chart_card, bg=_BG_CARD, highlightthickness=0, height=110)
        self._hist_chart_canvas.pack(fill="x", padx=8, pady=(2, 6))
        # Redraw whenever the canvas is resized (catches the first layout pass
        # where winfo_width() is still 1 at build time).
        self._hist_chart_canvas.bind(
            "<Configure>",
            lambda e: self._draw_history_chart(self._hist_chart_records))

        # Column header
        cols = tk.Frame(self._tab_history, bg=_BORDER_LT, pady=6)
        cols.pack(fill="x", padx=14, pady=(6, 0))
        for txt, w in [("DATE / TIME", 18), ("PROFILE", 22), ("DURATION", 10),
                        ("LOOPS", 7), ("AVG LOOP", 9), ("STOP REASON", 0)]:
            tk.Label(cols, text=txt, font=_F_LABEL, fg=_TEXT_MID,
                     bg=_BORDER_LT, width=w, anchor="w").pack(side="left", padx=6)

        # Scrollable rows area
        outer = tk.Frame(self._tab_history, bg=_BG)
        outer.pack(fill="both", expand=True, padx=14, pady=(0, 14))

        canvas = tk.Canvas(outer, bg=_BG, highlightthickness=0)
        sb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        self._history_rows_frame = tk.Frame(canvas, bg=_BG)
        _win = canvas.create_window((0, 0), window=self._history_rows_frame, anchor="nw")
        self._history_rows_frame.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(_win, width=e.width))

        def _hist_scroll(e: tk.Event) -> None:
            canvas.yview_scroll(-1 * (e.delta // 120), "units")

        self._hist_scroll_handler = _hist_scroll   # stored so refresh can rebind rows
        canvas.bind("<MouseWheel>", _hist_scroll)
        self._bind_scroll_tree(self._history_rows_frame, handler=_hist_scroll)

    def _refresh_history_tab(self) -> None:
        """Reload session_history.json and rebuild the history rows."""
        if not self._history_rows_frame:
            return
        for w in self._history_rows_frame.winfo_children():
            w.destroy()

        records: list = []
        if _HISTORY_FILE.exists():
            try:
                with open(_HISTORY_FILE, "r", encoding="utf-8") as f:
                    records = json.load(f)
            except (OSError, json.JSONDecodeError):
                pass

        if not records:
            tk.Label(self._history_rows_frame,
                     text="No sessions recorded yet. Run the bot to start tracking.",
                     font=_F_BODY, fg=_TEXT_DIM, bg=_BG, pady=30
                     ).pack(fill="x", padx=20)
            return

        for i, rec in enumerate(reversed(records)):
            row_bg = _ROW_A if i % 2 == 0 else _ROW_B
            row = tk.Frame(self._history_rows_frame, bg=row_bg, pady=6)
            row.pack(fill="x")
            tk.Frame(self._history_rows_frame, bg=_BORDER_LT, height=1).pack(fill="x")

            dur = rec.get("duration_s", 0)
            mm, ss = divmod(int(dur), 60)
            hh, mm = divmod(mm, 60)
            dur_txt = f"{hh:02d}:{mm:02d}:{ss:02d}" if hh else f"{mm:02d}:{ss:02d}"

            avg = rec.get("avg_loop_s", 0)
            avg_txt = f"{avg:.2f}s" if avg else "—"

            reason = rec.get("stop_reason", "—")
            reason_fg = _DANGER if "error" in reason.lower() or "fatal" in reason.lower() \
                        else _WARN if reason != "Manual stop" else _TEXT_DIM

            for txt, w, fg in [
                (rec.get("timestamp",  "—"), 18, _TEXT),
                (rec.get("profile",    "—"), 22, _ACCENT),
                (dur_txt,                   10, _TEXT),
                (str(rec.get("loops", "—")), 7, _SUCCESS),
                (avg_txt,                    9, _TEXT_MID),
                (reason,                     0, reason_fg),
            ]:
                tk.Label(row, text=txt, font=_F_MONO, fg=fg,
                         bg=row_bg, width=w, anchor="w").pack(side="left", padx=6)

        self._bind_scroll_tree(self._history_rows_frame,
                               handler=getattr(self, "_hist_scroll_handler", None))
        # Cache records so the <Configure> redraw callback can use them
        self._hist_chart_records = records
        self._draw_history_chart(records)

    def _draw_history_chart(self, records: list) -> None:
        """Draw a bar chart of the last 30 session durations onto the history canvas."""
        c = self._hist_chart_canvas
        if c is None:
            return
        c.delete("all")
        W = c.winfo_width()
        if W < 50:          # widget not yet laid out — <Configure> will fire again
            return
        H = 110
        PAD_L, PAD_R, PAD_T, PAD_B = 46, 10, 8, 22   # axis padding

        recent = records[-30:]            # last 30 sessions, oldest first
        if not recent:
            c.create_text(W // 2, H // 2, text="No data yet",
                          fill=_TEXT_DIM, font=_F_SMALL)
            return

        durations = [r.get("duration_s", 0) for r in recent]
        max_dur   = max(durations) or 1

        plot_w = W - PAD_L - PAD_R
        plot_h = H - PAD_T - PAD_B
        bar_w  = max(4, plot_w // len(recent) - 2)
        gap    = max(2, (plot_w - bar_w * len(recent)) // (len(recent) + 1))

        # Y-axis label
        for frac, lbl in [(0.0, _fmt_dur(max_dur)), (0.5, _fmt_dur(max_dur / 2)),
                          (1.0, "0")]:
            y = PAD_T + int(plot_h * frac)
            c.create_line(PAD_L - 3, y, PAD_L, y, fill=_BORDER_LT)
            c.create_text(PAD_L - 5, y, text=lbl, anchor="e",
                          fill=_TEXT_DIM, font=("Segoe UI", 7))

        # Baseline
        c.create_line(PAD_L, PAD_T + plot_h,
                      PAD_L + plot_w, PAD_T + plot_h,
                      fill=_BORDER_LT)

        for idx, (rec, dur) in enumerate(zip(recent, durations)):
            x0 = PAD_L + gap + idx * (bar_w + gap)
            bar_h = max(2, int(plot_h * dur / max_dur))
            y0 = PAD_T + plot_h - bar_h
            y1 = PAD_T + plot_h

            reason = rec.get("stop_reason", "")
            if "error" in reason.lower() or "fatal" in reason.lower():
                fill = _DANGER
            elif reason == "Manual stop":
                fill = _SUCCESS
            else:
                fill = _WARN

            c.create_rectangle(x0, y0, x0 + bar_w, y1,
                               fill=fill, outline="", width=0)

            # X-axis date tick (every 5th bar)
            if idx % 5 == 0:
                ts = rec.get("timestamp", "")[-5:]   # "HH:MM" slice
                c.create_text(x0 + bar_w // 2, PAD_T + plot_h + 4,
                               text=ts, anchor="n",
                               fill=_TEXT_DIM, font=("Segoe UI", 6))

        # Legend  (right → left, fixed offsets — never mutate W)
        legend_items = [(_SUCCESS, "normal"), (_WARN, "other"), (_DANGER, "error")]
        for li, (color, label) in enumerate(legend_items):
            lx = W - PAD_R - (li + 1) * 68
            c.create_rectangle(lx, H - PAD_B + 2, lx + 8, H - PAD_B + 10,
                               fill=color, outline="")
            c.create_text(lx + 10, H - PAD_B + 6, text=label, anchor="w",
                          fill=_TEXT_DIM, font=("Segoe UI", 7))

    def _clear_history(self) -> None:
        """Delete all session history records after confirmation."""
        if not messagebox.askyesno("Clear History",
                                   "Delete all session history records?", icon="warning"):
            return
        try:
            if _HISTORY_FILE.exists():
                _HISTORY_FILE.unlink()
        except OSError as e:
            _log.warning("Could not clear history: %s", e)
        self._refresh_history_tab()

    # ── Template Manager ─────────────────────────────────────────────────────
    def _build_templates_tab(self) -> None:
        """Build the Templates tab with a scrollable grid of template cards."""
        from tkinter import filedialog
        header = tk.Frame(self._tab_templates, bg=_BG_CARD)
        header.pack(fill="x", padx=14, pady=(14, 0))
        tk.Label(header, text="TEMPLATE MANAGER",
                 font=_F_HEAD, fg=_ACCENT, bg=_BG_CARD).pack(side="left", padx=8, pady=8)
        HoverButton(header, text="↺  REFRESH", command=self._refresh_templates_tab,
                    font=_F_SMALL, bg=_ACCENT_MU, fg=_ACCENT,
                    hover_bg=_ACCENT, hover_fg="#000", pady=4, padx=10
                    ).pack(side="right", padx=(4, 8))
        HoverButton(header, text="📂  IMPORT", command=self._template_import,
                    font=_F_SMALL, bg=_BG_HL, fg=_TEXT,
                    hover_bg=_BG_CARD, hover_fg=_ACCENT, pady=4, padx=10
                    ).pack(side="right", padx=4)
        HoverButton(header, text="📷  CAPTURE", command=self._template_capture,
                    font=_F_SMALL, bg=_BG_HL, fg=_ACCENT,
                    hover_bg=_ACCENT, hover_fg="#000", pady=4, padx=10
                    ).pack(side="right", padx=4)

        # Column headers
        cols = tk.Frame(self._tab_templates, bg=_BORDER_LT, pady=6)
        cols.pack(fill="x", padx=14, pady=(6, 0))
        for txt, w in [("PREVIEW", 10), ("FILENAME", 30), ("SIZE", 8),
                        ("USED IN STEPS", 14), ("ACTIONS", 0)]:
            tk.Label(cols, text=txt, font=_F_LABEL, fg=_TEXT_MID,
                     bg=_BORDER_LT, width=w, anchor="w").pack(side="left", padx=6)

        # Scrollable rows
        outer = tk.Frame(self._tab_templates, bg=_BG)
        outer.pack(fill="both", expand=True, padx=14, pady=(0, 14))

        canvas = tk.Canvas(outer, bg=_BG, highlightthickness=0)
        sb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        self._tpl_rows_frame = tk.Frame(canvas, bg=_BG)
        _win = canvas.create_window((0, 0), window=self._tpl_rows_frame, anchor="nw")
        self._tpl_rows_frame.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(_win, width=e.width))

        def _tpl_scroll(e: tk.Event) -> None:
            canvas.yview_scroll(-1 * (e.delta // 120), "units")
        canvas.bind("<MouseWheel>", _tpl_scroll)
        self._tpl_scroll_handler = _tpl_scroll

    def _count_template_usage(self) -> dict:
        """Return {stem_lower: count} of how many sequence steps reference each template."""
        counts: dict = {}
        for item in self._cfg.get("macro", {}).get("sequence", []):
            name = None
            if isinstance(item, str):
                if not item.upper().startswith(("POS:", "WAIT:", "KEY:")):
                    name = item.strip()
            elif isinstance(item, dict):
                tpl = (item.get("template") or "").strip()
                if tpl and not tpl.upper().startswith(("POS:", "WAIT:", "KEY:")):
                    name = tpl
            if name:
                stem = Path(name).stem.lower()
                counts[stem] = counts.get(stem, 0) + 1
        return counts

    def _refresh_templates_tab(self) -> None:
        """Reload templates/ and rebuild the template card rows."""
        if not self._tpl_rows_frame:
            return
        for w in self._tpl_rows_frame.winfo_children():
            w.destroy()

        tpl_dir = _HERE / "templates"
        files = sorted(
            p for p in tpl_dir.rglob("*")
            if p.suffix.lower() in (".png", ".bmp", ".jpg") and p.is_file()
        )
        usage = self._count_template_usage()

        if not files:
            tk.Label(self._tpl_rows_frame,
                     text="No templates found. Click CAPTURE or IMPORT to add some.",
                     font=_F_BODY, fg=_TEXT_DIM, bg=_BG, pady=30
                     ).pack(fill="x", padx=20)
            return

        for i, path in enumerate(files):
            row_bg = _ROW_A if i % 2 == 0 else _ROW_B
            row = tk.Frame(self._tpl_rows_frame, bg=row_bg, pady=4)
            row.pack(fill="x")
            tk.Frame(self._tpl_rows_frame, bg=_BORDER_LT, height=1).pack(fill="x")

            # Thumbnail
            thumb_lbl = tk.Label(row, bg=row_bg, width=10)
            thumb_lbl.pack(side="left", padx=(8, 6))
            try:
                img = Image.open(path)
                img.thumbnail((64, 48), Image.BILINEAR)
                photo = ImageTk.PhotoImage(img)
                thumb_lbl.config(image=photo)
                thumb_lbl.image = photo  # keep reference
            except Exception:
                thumb_lbl.config(text="[?]", fg=_TEXT_DIM, font=_F_SMALL)

            # Info
            size_kb  = path.stat().st_size // 1024
            stem     = path.stem.lower()
            use_cnt  = usage.get(stem, 0)
            use_txt  = f"{use_cnt} step{'s' if use_cnt != 1 else ''}"
            use_fg   = _SUCCESS if use_cnt > 0 else _TEXT_DIM

            info = tk.Frame(row, bg=row_bg)
            info.pack(side="left", fill="x", expand=True, padx=4)
            tk.Label(info, text=path.name, font=_F_BODY, fg=_TEXT,
                     bg=row_bg, anchor="w").pack(anchor="w")
            tk.Label(info, text=f"{size_kb} KB  ·  {path.relative_to(tpl_dir).parent}",
                     font=_F_SMALL, fg=_TEXT_DIM, bg=row_bg, anchor="w").pack(anchor="w")

            # Usage badge
            tk.Label(row, text=use_txt, font=_F_LABEL, fg=use_fg,
                     bg=row_bg, width=14, anchor="w").pack(side="left", padx=6)

            # Action buttons
            btns = tk.Frame(row, bg=row_bg)
            btns.pack(side="right", padx=8)
            HoverButton(btns, text="Rename",
                        command=lambda p=path: self._template_rename(p),
                        font=_F_SMALL, bg=_BG_HL, fg=_TEXT_MID,
                        hover_bg=_BG_CARD, hover_fg=_ACCENT,
                        pady=3, padx=8).pack(side="left", padx=2)
            HoverButton(btns, text="Delete",
                        command=lambda p=path: self._template_delete(p),
                        font=_F_SMALL, bg="#1E0008", fg=_DANGER,
                        hover_bg=_DANGER, hover_fg="#fff",
                        pady=3, padx=8).pack(side="left", padx=2)

        self._bind_scroll_tree(self._tpl_rows_frame,
                               handler=getattr(self, "_tpl_scroll_handler", None))

    def _template_capture(self, on_saved=None) -> None:
        """Hide the window, grab the full screen, then open the selection overlay.

        Args:
            on_saved: Optional callable(stem: str) called after a successful save.
        """
        self.withdraw()
        self.update_idletasks()
        self.after(250, lambda: self._do_screen_grab(on_saved))

    def _do_screen_grab(self, on_saved=None) -> None:
        """Take the full-screen screenshot and hand off to the overlay."""
        try:
            screenshot = ImageGrab.grab()
        except Exception as e:
            self.deiconify()
            messagebox.showerror("Capture Failed",
                                 f"Could not grab screen:\n{e}", parent=self)
            return
        self.deiconify()
        self._show_capture_overlay(screenshot, on_saved)

    def _show_capture_overlay(self, screenshot: Image.Image, on_saved=None) -> None:
        """Full-screen rubber-band selection overlay."""
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()

        overlay = tk.Toplevel(self)
        overlay.overrideredirect(True)
        overlay.geometry(f"{sw}x{sh}+0+0")
        overlay.attributes("-topmost", True)

        photo = ImageTk.PhotoImage(screenshot)
        canvas = tk.Canvas(overlay, width=sw, height=sh,
                           highlightthickness=0, cursor="crosshair")
        canvas.pack(fill="both", expand=True)
        canvas.create_image(0, 0, anchor="nw", image=photo)
        canvas.image = photo  # prevent GC

        # Semi-transparent dim using stipple
        canvas.create_rectangle(0, 0, sw, sh,
                                 fill="black", stipple="gray50", outline="")

        # Instruction banner
        canvas.create_rectangle(sw // 2 - 220, 10, sw // 2 + 220, 46,
                                 fill="#0D111E", outline="#22D3EE", width=1)
        canvas.create_text(sw // 2, 28,
                           text="Drag to select a region  ·  ESC to cancel",
                           fill="#E2E8F0", font=("Segoe UI", 12, "bold"))

        sel: dict = {"x0": 0, "y0": 0, "rect": None}

        def on_press(e: tk.Event) -> None:
            sel["x0"], sel["y0"] = e.x, e.y
            if sel["rect"]:
                canvas.delete(sel["rect"])
                sel["rect"] = None

        def on_drag(e: tk.Event) -> None:
            if sel["rect"]:
                canvas.delete(sel["rect"])
            sel["rect"] = canvas.create_rectangle(
                sel["x0"], sel["y0"], e.x, e.y,
                outline="#22D3EE", width=2,
            )

        def on_release(e: tk.Event) -> None:
            lx = min(sel["x0"], e.x)
            rx = max(sel["x0"], e.x)
            ty = min(sel["y0"], e.y)
            by = max(sel["y0"], e.y)
            overlay.destroy()
            if (rx - lx) < 4 or (by - ty) < 4:
                return  # Accidental click — ignore
            self._finish_capture(screenshot, lx, ty, rx, by, on_saved)

        canvas.bind("<ButtonPress-1>", on_press)
        canvas.bind("<B1-Motion>", on_drag)
        canvas.bind("<ButtonRelease-1>", on_release)
        overlay.bind("<Escape>", lambda _: overlay.destroy())
        overlay.focus_force()

    def _finish_capture(self, screenshot: Image.Image,
                        x0: int, y0: int, x1: int, y1: int,
                        on_saved=None) -> None:
        """Show a preview + name dialog, then save the cropped PNG to templates/."""
        crop = screenshot.crop((x0, y0, x1, y1))

        dlg = tk.Toplevel(self)
        dlg.title("Save Template")
        dlg.configure(bg=_BG_CARD)
        dlg.resizable(False, False)
        dlg.grab_set()
        self._show_popup(dlg, 360, 280)

        tk.Label(dlg, text="SAVE TEMPLATE",
                 font=_F_HEAD, fg=_ACCENT, bg=_BG_CARD).pack(pady=(16, 6))

        # Crop preview
        preview = crop.copy()
        preview.thumbnail((300, 120), Image.BILINEAR)
        ph = ImageTk.PhotoImage(preview)
        lbl_img = tk.Label(dlg, image=ph, bg=_BG_INSET, bd=0)
        lbl_img.image = ph
        lbl_img.pack(pady=(0, 8))

        # Name entry row
        row = tk.Frame(dlg, bg=_BG_CARD)
        row.pack(fill="x", padx=20)
        tk.Label(row, text="Name:", font=_F_LABEL, fg=_TEXT_MID,
                 bg=_BG_CARD, width=6).pack(side="left")
        name_var = tk.StringVar(value="template")
        ent = tk.Entry(row, textvariable=name_var, font=_F_MONO, width=22,
                       bg=_ENTRY_BG, fg=_TEXT, insertbackground=_TEXT,
                       relief="flat", highlightthickness=1,
                       highlightbackground=_BORDER)
        ent.pack(side="left", ipady=4)
        ent.select_range(0, "end")
        ent.focus_set()

        # Status / warning line
        lbl_warn = tk.Label(dlg, text="", font=_F_SMALL,
                            fg=_ORANGE, bg=_BG_CARD)
        lbl_warn.pack(pady=(4, 0))

        # Two-click overwrite guard
        _pending_overwrite: list[bool] = [False]

        def _reset_guard(*_) -> None:
            _pending_overwrite[0] = False
            lbl_warn.config(text="")

        name_var.trace_add("write", _reset_guard)

        def do_save() -> None:
            raw = name_var.get().strip()
            if not raw:
                lbl_warn.config(text="Name cannot be empty.")
                return
            stem = Path(raw).stem
            dest = _HERE / "templates" / f"{stem}.png"
            if dest.exists() and not _pending_overwrite[0]:
                lbl_warn.config(
                    text=f"'{stem}.png' already exists. Click Save again to overwrite.")
                _pending_overwrite[0] = True
                return
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                crop.save(dest, "PNG")
                _log.info("Template captured: %s", dest)
            except OSError as e:
                lbl_warn.config(text=f"Save failed: {e}", fg=_DANGER)
                return
            dlg.destroy()
            self._invalidate_template_cache()
            self._refresh_templates_tab()
            self._lbl_status.config(
                text=f"✓ Saved template '{stem}.png'", fg=_SUCCESS)
            self.after(4000, lambda: self._lbl_status.config(
                text="", fg=_TEXT_MID))
            if on_saved is not None:
                on_saved(stem)

        tk.Frame(dlg, bg=_BORDER_LT, height=1).pack(fill="x", pady=(8, 0))
        btns = tk.Frame(dlg, bg=_BG_CARD)
        btns.pack(fill="x", padx=20, pady=10)
        HoverButton(btns, text="Save", command=do_save,
                    font=_F_LABEL, bg=_ACCENT, fg="#000",
                    hover_bg="#00b8cc", hover_fg="#000",
                    pady=6, padx=20).pack(side="right", padx=(4, 0))
        HoverButton(btns, text="Cancel", command=dlg.destroy,
                    font=_F_LABEL, bg=_BG_HL, fg=_TEXT_MID,
                    hover_bg=_BG_CARD, hover_fg=_TEXT,
                    pady=6, padx=12).pack(side="right")

        dlg.bind("<Return>", lambda _: do_save())
        dlg.bind("<Escape>", lambda _: dlg.destroy())
        dlg.protocol("WM_DELETE_WINDOW", dlg.destroy)

    def _template_delete(self, path: Path) -> None:
        """Delete a template file after confirmation."""
        if not messagebox.askyesno("Delete Template",
                                   f"Delete '{path.name}'?\nThis cannot be undone.",
                                   icon="warning"):
            return
        try:
            path.unlink()
            self._invalidate_template_cache()
            self._refresh_templates_tab()
        except OSError as e:
            messagebox.showerror("Delete Failed", str(e))

    def _template_rename(self, path: Path) -> None:
        """Rename a template file, updating any sequence steps that reference it."""
        new_name = simpledialog.askstring(
            "Rename Template", "New filename (without extension):",
            initialvalue=path.stem)
        if not new_name or new_name.strip() == path.stem:
            return
        new_name = new_name.strip()
        new_path = path.with_name(new_name + path.suffix)
        if new_path.exists():
            messagebox.showerror("Rename Failed",
                                 f"'{new_path.name}' already exists.")
            return
        try:
            path.rename(new_path)
        except OSError as e:
            messagebox.showerror("Rename Failed", str(e))
            return
        # Update any steps that referenced the old name
        old_stem = path.stem.lower()
        seq = self._cfg.get("macro", {}).get("sequence", [])
        changed = False
        for j, item in enumerate(seq):
            if isinstance(item, str):
                if Path(item.strip()).stem.lower() == old_stem:
                    seq[j] = new_name + path.suffix
                    changed = True
            elif isinstance(item, dict):
                tpl = (item.get("template") or "").strip()
                if Path(tpl).stem.lower() == old_stem:
                    item["template"] = new_name + path.suffix
                    changed = True
        if changed:
            self._save_cfg_silent()
            self._refresh_macro_list()
        self._invalidate_template_cache()
        self._refresh_templates_tab()
        _log.info("Template renamed: %s → %s", path.name, new_path.name)

    def _template_import(self) -> None:
        """Open a file dialog to copy image files into the templates folder."""
        from tkinter import filedialog
        paths = filedialog.askopenfilenames(
            title="Import Templates",
            filetypes=[("Image files", "*.png *.bmp *.jpg *.jpeg"), ("All files", "*.*")])
        if not paths:
            return
        tpl_dir = _HERE / "templates"
        imported = 0
        for src in paths:
            dst = tpl_dir / Path(src).name
            if dst.exists():
                if not messagebox.askyesno("Overwrite?",
                                           f"'{Path(src).name}' already exists. Overwrite?"):
                    continue
            try:
                shutil.copy2(src, dst)
                imported += 1
            except OSError as e:
                _log.warning("Could not import '%s': %s", src, e)
        self._invalidate_template_cache()
        self._refresh_templates_tab()
        _log.info("Imported %d template(s).", imported)

    # ── Scheduler ────────────────────────────────────────────────────────────
    def _build_scheduler_ui(self, parent: tk.Frame) -> None:
        """Build the compact scheduler controls in the sidebar."""
        self._sched_enabled_var = tk.BooleanVar(value=False)
        self._sched_start_var   = tk.StringVar(value="")
        self._sched_stop_var    = tk.StringVar(value="")

        row1 = tk.Frame(parent, bg=_BG_CARD)
        row1.pack(fill="x", padx=14, pady=(2, 0))
        _sched_lbl = tk.Label(row1, text="✗", width=2, fg="#64748B", bg="#0D111E",
                              font=("Segoe UI", 10, "bold"), relief="solid", bd=1,
                              cursor="hand2", padx=2, pady=0)
        _sched_lbl.pack(side="left")
        tk.Label(row1, text="  Enable scheduler", font=_F_SMALL,
                 fg=_TEXT, bg=_BG_CARD, cursor="hand2").pack(side="left")

        def _toggle_sched(lbl=_sched_lbl, var=self._sched_enabled_var):
            new_val = not var.get()
            var.set(new_val)
            if new_val:
                lbl.config(text="✓", fg="#10B981", bg="#0B2E1E")
            else:
                lbl.config(text="✗", fg="#64748B", bg="#0D111E")
            self._sched_toggle()

        _sched_lbl.bind("<Button-1>", lambda e: _toggle_sched() or "break")
        row1.bind("<Button-1>",      lambda e: _toggle_sched())

        row2 = tk.Frame(parent, bg=_BG_CARD)
        row2.pack(fill="x", padx=14, pady=2)
        tk.Label(row2, text="Start:", font=_F_SMALL, fg=_TEXT_DIM,
                 bg=_BG_CARD, width=5, anchor="w").pack(side="left")
        tk.Entry(row2, textvariable=self._sched_start_var, width=6,
                 bg=_ENTRY_BG, fg=_SUCCESS, font=_F_MONO,
                 insertbackground=_SUCCESS, relief="flat",
                 highlightthickness=1, highlightbackground=_BORDER
                 ).pack(side="left", padx=(0, 8))
        tk.Label(row2, text="Stop:", font=_F_SMALL, fg=_TEXT_DIM,
                 bg=_BG_CARD, width=5, anchor="w").pack(side="left")
        tk.Entry(row2, textvariable=self._sched_stop_var, width=6,
                 bg=_ENTRY_BG, fg=_DANGER, font=_F_MONO,
                 insertbackground=_DANGER, relief="flat",
                 highlightthickness=1, highlightbackground=_BORDER
                 ).pack(side="left")

        self._sched_lbl = tk.Label(parent, text="HH:MM format  ·  24-hour",
                                   font=_F_SMALL, fg=_TEXT_DIM, bg=_BG_CARD)
        self._sched_lbl.pack(padx=14, anchor="w", pady=(0, 4))

    @staticmethod
    def _valid_hhmm(value: str) -> bool:
        """Return True if *value* is a non-empty, valid HH:MM (24-hour) string."""
        v = value.strip()
        if not v:
            return True   # empty = not set, which is fine
        try:
            datetime.datetime.strptime(v, "%H:%M")
            return True
        except ValueError:
            return False

    def _sched_toggle(self) -> None:
        """Validate time fields then start or stop the scheduler poll loop."""
        # Cancel any existing poll chain before potentially starting a new one.
        # This prevents multiple parallel after() chains accumulating on repeated toggles.
        if self._sched_poll_job is not None:
            self.after_cancel(self._sched_poll_job)
            self._sched_poll_job = None

        if not (self._sched_enabled_var and self._sched_enabled_var.get()):
            if self._sched_lbl:
                self._sched_lbl.config(text="HH:MM format  ·  24-hour", fg=_TEXT_DIM)
            return

        start_t = (self._sched_start_var.get() or "").strip()
        stop_t  = (self._sched_stop_var.get()  or "").strip()

        # At least one time must be set
        if not start_t and not stop_t:
            if self._sched_lbl:
                self._sched_lbl.config(text="⚠  Enter at least one time", fg=_WARN)
            self._sched_enabled_var.set(False)
            return

        # Both supplied times must be valid HH:MM
        bad = [t for t in (start_t, stop_t) if t and not self._valid_hhmm(t)]
        if bad:
            if self._sched_lbl:
                self._sched_lbl.config(
                    text=f"⚠  Invalid time: {', '.join(bad)}  (use HH:MM)",
                    fg=_DANGER)
            self._sched_enabled_var.set(False)
            return

        # All good — start the poll loop
        self._sched_poll()
        parts = []
        if start_t: parts.append(f"start {start_t}")
        if stop_t:  parts.append(f"stop {stop_t}")
        if self._sched_lbl:
            self._sched_lbl.config(text=f"⏰  {' · '.join(parts)}", fg=_SUCCESS)

    def _sched_poll(self) -> None:
        """Check current time every 15 s; auto-start or auto-stop the bot."""
        self._sched_poll_job = None   # clear handle — we're running now
        if not (self._sched_enabled_var and self._sched_enabled_var.get()):
            return
        now = datetime.datetime.now().strftime("%H:%M")

        start_t = (self._sched_start_var.get() or "").strip()
        stop_t  = (self._sched_stop_var.get()  or "").strip()

        if start_t and now == start_t and not self._is_running:
            _log.info("Scheduler: auto-starting at %s", now)
            self._start_bot()

        if stop_t and now == stop_t and self._is_running:
            _log.info("Scheduler: auto-stopping at %s", now)
            self._stop_bot()

        # Store the job handle so _sched_toggle can cancel it if the user toggles off
        self._sched_poll_job = self.after(15_000, self._sched_poll)

    def _on_tab_changed(self, event: tk.Event | None = None) -> None:
        """Refresh dynamic tab content when the user switches to it.
        Kept for backward compatibility; actual switching goes via _switch_tab.
        """
        if self._active_tab_idx < len(self._TAB_DEFS):
            name = self._TAB_DEFS[self._active_tab_idx][2]
            if name == "HISTORY":
                self._refresh_history_tab()
            elif name == "TEMPLATES":
                self._refresh_templates_tab()

    # ── Config save ───────────────────────────────────────────────────────────
    def _save_config(self) -> None:
        """Validate all settings fields, write to config.json, then flash a status confirmation."""
        errors: list[str] = []
        for k, var in self._cfg_vars.items():
            val = var.get()
            if k in _NUMERIC_CFG_KEYS:
                expected = _NUMERIC_CFG_KEYS[k]
                try:
                    val = expected(val)
                except (ValueError, TypeError):
                    errors.append(f"  • {k}: '{val}' is not a valid {expected.__name__}")
        if errors:
            messagebox.showerror(
                "Invalid Settings",
                "The following fields contain invalid values:\n\n" + "\n".join(errors),
            )
            return

        for k, var in self._cfg_vars.items():
            val = var.get()
            # Coerce to the correct numeric type when storing
            if k in _NUMERIC_CFG_KEYS:
                val = _NUMERIC_CFG_KEYS[k](val)
            d = self._cfg; pts = k.split(".")
            for p in pts[:-1]: d = d.setdefault(p, {})
            d[pts[-1]] = val
        self._save_cfg_silent()
        apply_path_resolution(self._cfg)
        self._setup_hotkeys()
        # Reflect the (possibly new) window title in the status bar
        if self._sb_window:
            self._sb_window.config(
                text=f"Target: {self._cfg.get('window_title', '—')}")
        self._lbl_status.config(text="✓ Settings saved", fg=_SUCCESS)
        self.after(4000, lambda: self._lbl_status.config(
            text="● RUNNING" if self._is_running else "● IDLE",
            fg=_SUCCESS if self._is_running else _TEXT_MID,
        ))

    def _refresh_cfg_panel(self) -> None:
        """Sync all UI config vars to the current self._cfg after a profile load."""
        for k, var in self._cfg_vars.items():
            val = self._get_cfg(k)
            try:
                if isinstance(var, tk.BooleanVar): var.set(bool(val))
                else: var.set(str(val))
            except tk.TclError:
                pass
        # Sync secondary profile dropdown (not in _cfg_vars)
        if self._sec_prof_var is not None:
            new_prof = str(self._cfg.get("secondary_macro", {}).get("profile", ""))
            profiles  = list_profiles(str(_HERE))
            if new_prof in profiles:
                try:
                    self._sec_prof_var.set(new_prof)
                except tk.TclError:
                    pass

    def _save_cfg_silent(self) -> None:
        """Write config.json silently (with backup), used by all auto-save paths.

        self._cfg keeps resolved absolute paths for runtime use.  We convert
        them back to relative paths before writing to disk so the saved file
        works on any machine regardless of where the app folder lives.
        """
        cfg_path = _HERE / "config.json"
        if cfg_path.exists():
            try:
                shutil.copy2(cfg_path, _HERE / "config.json.bak")
            except OSError:
                pass
        saveable = make_paths_relative(self._cfg)          # portable copy
        with open(cfg_path, "w", encoding="utf-8") as f:   # explicit encoding
            json.dump(saveable, f, indent=2)

    def _show_tpl_thumb(self, event: tk.Event, tpl_name: str) -> None:
        """Show a floating thumbnail of the template image near the cursor."""
        self._hide_tpl_thumb(None)   # close any existing one
        stem = Path(tpl_name).stem.lower()
        img_path: Path | None = None
        for ext in (".png", ".bmp"):
            candidates = list((_HERE / "templates").rglob(f"{stem}{ext}"))
            if candidates:
                img_path = candidates[0]
                break
        if img_path is None:
            return
        try:
            pil = Image.open(img_path)
            pil.thumbnail((180, 180))
            tk_img = ImageTk.PhotoImage(pil)
        except Exception:
            return

        popup = tk.Toplevel(self)
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        popup.configure(bg=_BG_CARD)
        # Position near cursor
        x = event.widget.winfo_rootx() + event.widget.winfo_width() + 6
        y = event.widget.winfo_rooty()
        popup.geometry(f"+{x}+{y}")
        lbl = tk.Label(popup, image=tk_img, bg=_BG_CARD,
                       relief="flat", bd=0,
                       highlightthickness=1, highlightbackground=_BORDER_LT)
        lbl.image = tk_img   # keep reference
        lbl.pack()
        # Also show the filename
        tk.Label(popup, text=img_path.name, bg=_BG_CARD, fg=_TEXT_DIM,
                 font=_F_SMALL).pack(pady=(0, 4))
        self._tpl_thumb_popup = popup

    def _hide_tpl_thumb(self, event) -> None:
        """Destroy the thumbnail popup if open."""
        popup = getattr(self, "_tpl_thumb_popup", None)
        if popup:
            try: popup.destroy()
            except Exception: pass
            self._tpl_thumb_popup = None

    # ── Remote Dashboard ──────────────────────────────────────────────────────

    def _start_remote_dashboard(self) -> None:
        """Read config, start the HTTP dashboard server, update the URL label."""
        remote_cfg = self._cfg.get("remote", {})
        if not remote_cfg.get("enabled", True):
            return
        port = int(remote_cfg.get("port", 8765) or 8765)
        dash = RemoteDashboard(port=port)
        if dash.start():
            self._remote_dash = dash
            url = f"http://{RemoteDashboard.local_ip()}:{port}"
            _log.info("Remote dashboard active → %s", url)
            if self._lbl_remote_url is not None:
                self._lbl_remote_url.config(text=f"📡  {url}")
        else:
            _log.warning("Remote dashboard could not bind to port %d.", port)
            if self._lbl_remote_url is not None:
                self._lbl_remote_url.config(
                    text=f"⚠  Remote dashboard unavailable (port {port} in use)",
                    fg=_WARN)

    def _on_close(self) -> None:
        if self._is_running:
            if not messagebox.askyesno(
                "Bot is Running",
                "The bot is currently active.\n\nStop it and close the app?",
                icon="warning",
            ):
                return
        self._stop_event.set()
        self._cleanup_log_on_exit()
        self.destroy()

    def _cleanup_log_on_exit(self) -> None:
        """Shut down the remote dashboard, close log file handles, then delete
        the log file and any rotated backups so the next session starts clean."""
        # Stop the HTTP server first (releases thread + port)
        if self._remote_dash is not None:
            try:
                self._remote_dash.stop()
            except Exception:
                pass
            self._remote_dash = None

        log_f = Path(
            self._cfg.get("logging", {}).get("log_file", str(_HERE / "bot.log"))
        )
        # Shut down every handler attached to the 'G Panel' root logger so the
        # RotatingFileHandler releases its file handle before we try to delete it.
        root = logging.getLogger("G Panel")
        for h in list(root.handlers):
            try:
                root.removeHandler(h)
                h.close()
            except Exception:
                pass
        # Delete the main log and any rotated backups (.1 … .N)
        backup_n = int(self._cfg.get("logging", {}).get("backup_count", 3))
        for _n in range(backup_n + 1):
            p = Path(str(log_f) + ("" if _n == 0 else f".{_n}"))
            try:
                if p.exists():
                    p.unlink()
            except OSError:
                pass


if __name__ == "__main__":
    GPanelApp().mainloop()
