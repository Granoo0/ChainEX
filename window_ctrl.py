"""
window_ctrl.py — Low-level Windows API interface.

HOW BACKGROUND INPUT WORKS
───────────────────────────
When a game window is minimised or covered by other windows, global input
simulation (SendInput, mouse_event) sends events to *whichever window currently
has focus* — not to the game.

PostMessage() / SendMessage() bypass focus entirely: they inject WM_LBUTTONDOWN,
WM_LBUTTONUP, WM_KEYDOWN, etc. directly into the target window's message queue,
exactly as if you had physically clicked inside it while it was focused.

The game's own message-pump processes these events normally, so the bot can
automate the game while you use other applications in the foreground.

WINDOW CAPTURE
──────────────
BitBlt captures only what is currently on-screen (fails for minimised windows).
PrintWindow() / WM_PRINT copies the window's content using its own rendering
path, working even when the window is minimised or fully occluded.

We try PrintWindow first and fall back to BitBlt for games that don't respond
to WM_PRINT (e.g., some OpenGL / Vulkan renderers).
"""

import ctypes
import ctypes.wintypes as wt
import os
import time
from typing import Optional, Tuple
from PIL import ImageGrab

import numpy as np
import cv2

# pywin32 — install with:  pip install pywin32
import win32api
import win32con
import win32gui
import win32process
import win32ui

from bot_logger import get_logger

_log = get_logger("WindowCtrl")


# ─────────────────────────────────────────────────────────────────────────────
# Windows message constants not always exported by win32con
# ─────────────────────────────────────────────────────────────────────────────
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP   = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP   = 0x0205
WM_KEYDOWN     = 0x0100
WM_KEYUP       = 0x0101
WM_CHAR        = 0x0102

# PrintWindow flags
PW_CLIENTONLY = 0x00000001
PW_RENDERFULLCONTENT = 0x00000002   # Windows 8.1+; forces GPU flush


def _MAKELPARAM(x: int, y: int) -> int:
    """Pack (x, y) screen coordinates into a single LPARAM value."""
    return (y << 16) | (x & 0xFFFF)


# ─────────────────────────────────────────────────────────────────────────────
class WindowController:
    """
    Encapsulates all interactions with a single game window:
      - Finding the window by title substring
      - Capturing its pixel contents as a NumPy array (BGR, same as OpenCV)
      - Sending mouse clicks and key presses via PostMessage
    """

    def __init__(self, title_substring: str) -> None:
        self.title_substring = title_substring.lower()
        self.hwnd:  int  = 0
        self.title: str  = ""
        self._client_rect: Optional[Tuple[int, int, int, int]] = None   # (x, y, w, h)
        self._last_capture_method: str = "bitblt" # Try the fastest first

    # ── Window Discovery ──────────────────────────────────────────────────

    def find_window(self) -> bool:
        """
        Enumerate all top-level windows and find one whose title contains
        *title_substring* (case-insensitive).  Returns True if found.
        Own-process windows are skipped so the bot never attaches to itself.
        """
        found_hwnd  = 0
        found_title = ""
        own_pid     = os.getpid()

        def _enum_callback(hwnd: int, _lParam) -> bool:
            nonlocal found_hwnd, found_title
            if win32gui.IsWindowVisible(hwnd):
                try:
                    _, pid = win32process.GetWindowThreadProcessId(hwnd)
                    if pid == own_pid:
                        return True  # Never attach to our own windows
                except Exception:
                    return True
                title = win32gui.GetWindowText(hwnd)
                if self.title_substring in title.lower():
                    found_hwnd  = hwnd
                    found_title = title
                    return False   # Stop enumeration
            return True            # Continue

        win32gui.EnumWindows(_enum_callback, None)

        if found_hwnd:
            self.hwnd  = found_hwnd
            self.title = found_title
            self._refresh_client_rect()
            return True
        return False

    def is_alive(self) -> bool:
        """Return True if the window handle is still valid."""
        return bool(win32gui.IsWindow(self.hwnd))

    def get_window_title(self) -> str:
        """Current window title (may differ slightly from match substring)."""
        try:
            return (win32gui.GetWindowText(self.hwnd) or "").strip()
        except Exception:
            return ""

    def bring_to_foreground(self) -> bool:
        """
        Try to focus the target window (some games only accept input when focused).
        Uses AttachThreadInput workaround common on Windows.
        """
        if not self.is_alive():
            return False
        try:
            win32gui.ShowWindow(self.hwnd, win32con.SW_RESTORE)
            fg = win32gui.GetForegroundWindow()
            tid_fg = win32process.GetWindowThreadProcessId(fg)[0]
            tid_me = win32process.GetWindowThreadProcessId(self.hwnd)[0]
            if tid_fg and tid_me:
                win32process.AttachThreadInput(tid_fg, tid_me, True)
            try:
                win32gui.SetForegroundWindow(self.hwnd)
            finally:
                if tid_fg and tid_me:
                    win32process.AttachThreadInput(tid_fg, tid_me, False)
            _log.debug("bring_to_foreground OK hwnd=0x%X", self.hwnd)
            return True
        except Exception as exc:
            _log.warning("bring_to_foreground failed: %s", exc)
            return False

    def _refresh_client_rect(self) -> None:
        """Cache the client-area rectangle (top-left in screen coordinates)."""
        try:
            # GetClientRect gives size relative to window; ClientToScreen gives origin
            rect = win32gui.GetClientRect(self.hwnd)
            origin = win32gui.ClientToScreen(self.hwnd, (0, 0))
            self._client_rect = (origin[0], origin[1], rect[2], rect[3])
        except Exception as exc:
            _log.warning("Could not refresh client rect: %s", exc)

    @property
    def client_width(self) -> int:
        return self._client_rect[2] if self._client_rect else 0

    @property
    def client_height(self) -> int:
        return self._client_rect[3] if self._client_rect else 0

    # ── Screen Capture ────────────────────────────────────────────────────

    def capture(self) -> Optional[np.ndarray]:
        """
        Capture the game window's client area as a BGR NumPy array.
        Uses a sticky strategy to avoid repeatedly trying failing methods.
        """
        if not self.is_alive():
            _log.error("Window handle no longer valid.")
            return None

        self._refresh_client_rect()
        w, h = self.client_width, self.client_height
        if w <= 0 or h <= 0:
            return None

        methods = ["bitblt", "printwindow", "imagegrab"]
        # Reorder to try the last successful method first
        if self._last_capture_method in methods:
            methods.remove(self._last_capture_method)
            methods.insert(0, self._last_capture_method)

        last_exc: Exception | None = None
        for method in methods:
            try:
                img = None
                if method == "bitblt":
                    img = self._capture_bitblt(w, h)
                    if np.max(img) == 0: raise RuntimeError("Black frame")
                elif method == "printwindow":
                    img = self._capture_printwindow(w, h)
                elif method == "imagegrab":
                    x, y, cw, ch = self._client_rect
                    pil_img = ImageGrab.grab(bbox=(x, y, x + cw, y + ch))
                    img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)

                if img is not None:
                    self._last_capture_method = method
                    return img
            except Exception as exc:
                last_exc = exc
                continue

        _log.warning("All capture methods failed (last error: %s).", last_exc)
        return None

    def _capture_printwindow(self, w: int, h: int) -> np.ndarray:
        """
        Use PrintWindow to copy the window's rendering into a memory DC.
        Works even for minimised / occluded windows on most DWM-composited games.
        """
        hwnd_dc = win32gui.GetWindowDC(self.hwnd)
        src_dc  = win32ui.CreateDCFromHandle(hwnd_dc)
        mem_dc  = src_dc.CreateCompatibleDC()
        bmp     = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(src_dc, w, h)
        mem_dc.SelectObject(bmp)

        try:
            # PW_RENDERFULLCONTENT flushes GPU-rendered content (DirectX/OpenGL).
            # Falls back to PW_CLIENTONLY for Windows 7 compatibility.
            result = ctypes.windll.user32.PrintWindow(
                self.hwnd, mem_dc.GetSafeHdc(),
                PW_CLIENTONLY | PW_RENDERFULLCONTENT
            )
            if not result:
                result = ctypes.windll.user32.PrintWindow(
                    self.hwnd, mem_dc.GetSafeHdc(), PW_CLIENTONLY
                )
            if not result:
                raise RuntimeError("PrintWindow returned 0")

            bmp_info = bmp.GetInfo()
            raw      = bmp.GetBitmapBits(True)

            # Convert raw BGRA bytes → (H, W, 4) NumPy array → drop alpha → BGR
            img = np.frombuffer(raw, dtype=np.uint8).reshape(
                bmp_info["bmHeight"], bmp_info["bmWidth"], 4
            )
            return img[:, :, :3].copy()   # Keep only B, G, R channels

        finally:
            # Always release GDI objects — even if PrintWindow or reshape raises.
            try: mem_dc.DeleteDC()
            except Exception: pass
            try: src_dc.DeleteDC()
            except Exception: pass
            try: win32gui.ReleaseDC(self.hwnd, hwnd_dc)
            except Exception: pass
            try: win32gui.DeleteObject(bmp.GetHandle())
            except Exception: pass

    def _capture_bitblt(self, w: int, h: int) -> np.ndarray:
        """
        Fallback: BitBlt from the screen at the window's screen coordinates.
        Only captures pixels that are currently visible on your monitor.
        """
        x, y = self._client_rect[:2]
        hwnd_dc = win32gui.GetDC(0)                      # Desktop DC
        src_dc  = win32ui.CreateDCFromHandle(hwnd_dc)
        mem_dc  = src_dc.CreateCompatibleDC()
        bmp     = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(src_dc, w, h)
        mem_dc.SelectObject(bmp)

        try:
            mem_dc.BitBlt((0, 0), (w, h), src_dc, (x, y), win32con.SRCCOPY)

            bmp_info = bmp.GetInfo()
            raw      = bmp.GetBitmapBits(True)
            return np.frombuffer(raw, dtype=np.uint8).reshape(
                bmp_info["bmHeight"], bmp_info["bmWidth"], 4
            )[:, :, :3].copy()

        finally:
            # Always release GDI objects — even if BitBlt or reshape raises.
            try: mem_dc.DeleteDC()
            except Exception: pass
            try: src_dc.DeleteDC()
            except Exception: pass
            try: win32gui.ReleaseDC(0, hwnd_dc)
            except Exception: pass
            try: win32gui.DeleteObject(bmp.GetHandle())
            except Exception: pass

    def capture_full_screen(self) -> Optional[np.ndarray]:
        """Capture the entire monitor as a BGR NumPy array."""
        try:
            pil_img = ImageGrab.grab()
            return cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
        except Exception as exc:
            _log.error("Full screen capture failed: %s", exc)
            return None

    # ── Mouse Input ───────────────────────────────────────────────────────

    def click(
        self,
        x: int,
        y: int,
        *,
        button: str = "left",
        hold_ms: int = 50,
    ) -> None:
        """
        Post a mouse button press + release to the game window at client
        coordinates (x, y).

        PostMessage is asynchronous — it places the message in the window's
        queue and returns immediately.  We add a small sleep between down and
        up to mimic human timing and give the game time to process the press.

        Args:
            x, y      : Client-area coordinates (0,0 = top-left of the game).
            button    : "left" or "right".
            hold_ms   : Milliseconds to hold the button before releasing.
        """
        if not self.is_alive():
            _log.warning("click() called on dead window handle.")
            return

        lparam = _MAKELPARAM(x, y)

        if button == "left":
            down_msg, up_msg = WM_LBUTTONDOWN, WM_LBUTTONUP
            wparam = win32con.MK_LBUTTON
        else:
            down_msg, up_msg = WM_RBUTTONDOWN, WM_RBUTTONUP
            wparam = win32con.MK_RBUTTON

        win32api.PostMessage(self.hwnd, down_msg, wparam, lparam)
        time.sleep(hold_ms / 1000.0)
        win32api.PostMessage(self.hwnd, up_msg,   0,      lparam)

        _log.debug("click(%d, %d, %s)", x, y, button)

    def click_region_center(self, region: Tuple[int, int, int, int], **kwargs) -> None:
        """Click the centre of (x, y, w, h) region."""
        x, y, w, h = region
        self.click(x + w // 2, y + h // 2, **kwargs)

    def click_global(self, x: int, y: int, hold_ms: int = 50) -> None:
        """
        Synthesizes a physical mouse click by moving the REAL mouse cursor to
        the target (x, y) client coordinates and simulating input.
        Required for games that block background PostMessage.
        """
        # Always refresh rect before global click to handle window movement
        self._refresh_client_rect()
        if not self._client_rect: return
        
        # Absolute screen coordinates
        abs_x = self._client_rect[0] + int(x)
        abs_y = self._client_rect[1] + int(y)
        
        _log.debug("Global Click Client=(%d, %d) -> Absolute=(%d, %d)", x, y, abs_x, abs_y)

        try:
            # 1. Move
            win32api.SetCursorPos((abs_x, abs_y))
            time.sleep(0.05) # Increased delay for Windows input processing
            # 2. Press
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            time.sleep(hold_ms / 1000.0)
            # 3. Release
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        except Exception as exc:
            _log.error("Global click failed: %s", exc)

    # ── Keyboard Input ────────────────────────────────────────────────────

    def key_press(self, vk_code: int, hold_ms: int = 50) -> None:
        """
        Post WM_KEYDOWN + WM_KEYUP for a virtual-key code to the game window.

        Common VK codes: win32con.VK_RETURN, win32con.VK_ESCAPE,
        ord('1'), ord('A'), etc.
        """
        if not self.is_alive():
            return
        win32api.PostMessage(self.hwnd, WM_KEYDOWN, vk_code, 0)
        time.sleep(hold_ms / 1000.0)
        win32api.PostMessage(self.hwnd, WM_KEYUP,   vk_code, 0)
        _log.debug("key_press(VK=0x%X)", vk_code)

    def send_char(self, char: str) -> None:
        """Post a WM_CHAR for a single printable character."""
        if not self.is_alive():
            return
        win32api.PostMessage(self.hwnd, WM_CHAR, ord(char), 0)
