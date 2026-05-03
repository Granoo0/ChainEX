import threading
import time
import queue
from pathlib import Path
from typing import Dict

import cv2
import numpy as np

from window_ctrl      import WindowController
from image_recognizer import ImageRecognizer
from bot_logger       import get_logger
from states           import BaseState, StateName, StateTransition, MacroState

_log = get_logger("Engine")

class BotEngine:
    """Core state machine engine for G Panel."""
    def __init__(
        self,
        wc:              WindowController,
        cfg:             dict,
        stop_event:      threading.Event,
        frame_queue:     "queue.Queue | None" = None,
        stop_after_loop: "threading.Event | None" = None,
        stats_queue:     "queue.Queue | None"      = None,
        pause_event:     "threading.Event | None"  = None,
        step_ref:        "list | None"             = None,
    ) -> None:
        self._wc              = wc
        self._cfg             = cfg
        self._stop_event      = stop_event
        self._frame_queue     = frame_queue
        self._stop_after_loop = stop_after_loop
        self._stats_queue     = stats_queue
        self._pause_event     = pause_event
        self._step_ref        = step_ref

        # ── Vision System ────────────────────────────────────────────────
        v_cfg = cfg.get("vision", {})
        configured_template_dir = v_cfg.get("template_dir", "templates")
        template_dir = Path(configured_template_dir)
        if not template_dir.is_absolute():
            template_dir = Path(__file__).resolve().parent / template_dir
        self._vision = ImageRecognizer(
            template_dir         = str(template_dir),
            threshold            = v_cfg.get("match_threshold", 0.5),
            use_grayscale        = v_cfg.get("use_grayscale", True),
            templates_meta_file  = v_cfg.get("templates_meta_file"),
        )
        if self._vision.template_count == 0:
            _log.error(
                "No templates loaded from '%s'. Bot cannot detect or click targets.",
                template_dir,
            )

        # ── Pure Macro Engine Initialization ─────────────────────────────
        self._macro_state = MacroState(self._wc, self._vision, self._cfg,
                                       stop_after_loop=self._stop_after_loop,
                                       stats_queue=self._stats_queue,
                                       pause_event=self._pause_event,
                                       step_ref=self._step_ref)
        self._current = StateName.MACRO

        self._poll_interval = 0.05  # Hard-locked to 50ms for maximum Macro speed
        self._last_cycle_debug = 0.0
        self._cycle_debug_interval_s = max(
            0.5,
            cfg.get("timing", {}).get("cycle_debug_interval_ms", 2000) / 1000.0
        )

        # Runtime diagnostics (capture health, debug frames)
        self._engine_start = 0.0
        self._last_debug_ms = 0.0
        self._black_frame_streak = 0
        self._last_frame_variance: float | None = None
        self._static_screen_since: float | None = None


    def _maybe_debug_save(self, frame: np.ndarray) -> None:
        dbg = self._cfg.get("debug", {})
        if not dbg.get("enabled"):
            return
        now_ms = time.monotonic() * 1000.0
        interval = max(50.0, float(dbg.get("interval_ms", 500)))
        if now_ms - self._last_debug_ms < interval:
            return
        self._last_debug_ms = now_ms

        out_dir = Path(dbg.get("save_dir", "debug"))
        out_dir.mkdir(parents=True, exist_ok=True)
        vis = frame.copy()
        for name in dbg.get("templates_to_draw") or []:
            hit = self._vision.find(vis, str(name))
            if hit:
                _, conf, bbox = hit
                x, y, w, h = bbox
                cv2.rectangle(vis, (x, y), (x + w, y + h), (0, 255, 0), 2)
                cv2.putText(
                    vis,
                    f"{name} {conf:.2f}",
                    (x, max(22, y - 4)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    (0, 255, 0),
                    1,
                    cv2.LINE_AA,
                )
        cv2.imwrite(str(out_dir / "last_debug.png"), vis)
        if dbg.get("opencv_window"):
            try:
                cv2.imshow("G Panel Debug", vis)
                cv2.waitKey(1)
            except Exception:
                pass



    def run(self) -> None:
        _log.info("G Panel Engine Active. Running Macro Mode.")
        self._engine_start = time.monotonic()

        while not self._stop_event.is_set():
            loop_start = time.monotonic()

            max_rt = float(self._cfg.get("timing", {}).get("max_runtime_minutes", 0) or 0)
            if max_rt > 0 and (time.monotonic() - self._engine_start) >= max_rt * 60.0:
                _log.warning("Max runtime (%.1f min) reached — stopping.", max_rt)
                self._stop_event.set()
                break

            sub = str(self._cfg.get("window_title", "")).strip().lower()
            if sub and self._cfg.get("window", {}).get("pause_on_title_change", True):
                cur = self._wc.get_window_title()
                if cur and sub not in cur.lower():
                    _log.warning(
                        "Window title changed (no longer contains %r) — stopping.",
                        self._cfg.get("window_title"),
                    )
                    self._stop_event.set()
                    break

            # Optimization: Only capture if Macro logic or Debugging needs it
            needs_v = self._macro_state.needs_frame()
            is_debug = self._cfg.get("debug", {}).get("enabled", False)
            
            frame = None
            if needs_v or is_debug:
                frame = self._wc.capture()
                if frame is None:
                    if not self._wc.is_alive(): break
                    time.sleep(1.0); continue
                
                self._maybe_debug_save(frame)
                
                # Live Feed update (only happens if we captured a frame)
                if self._frame_queue:
                    # Drain stale frames (TOCTOU-safe: catch Empty in case another
                    # reader empties the queue between the empty() check and get).
                    try:
                        while True:
                            self._frame_queue.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        self._frame_queue.put_nowait(frame.copy())
                    except queue.Full:
                        pass

            try:
                transition = self._macro_state.run(frame)
                if not isinstance(transition, StateTransition):
                    transition = StateTransition.stay(reason="Invalid transition object", delay_ms=250)
                
                if transition.next_state == StateName.ERROR:
                    _log.error("Fatal state reached: %s", transition.reason or "unknown error")
                    self._stop_event.set()
                    break

                # Any transition away from MACRO/SELF means MacroState has finished
                # (e.g. secondary_switch loop-end, or repeat=False completion).
                # Break cleanly so the _run_engine finally-block can decide what to do.
                if transition.next_state not in (StateName.SELF, StateName.MACRO):
                    _log.info(
                        "Engine exiting cleanly: next=%s reason=%s",
                        transition.next_state.value,
                        transition.reason or "-",
                    )
                    # "Macro complete" = repeat=False finished naturally → route to _stop_bot,
                    # not _attempt_reconnect. Setting the stop event is the signal.
                    if transition.reason == "Macro complete":
                        self._stop_event.set()
                    break

                elapsed = time.monotonic() - loop_start
                now = time.monotonic()
                if now - self._last_cycle_debug >= self._cycle_debug_interval_s:
                    _log.debug(
                        "Cycle: %dms | state=%s | reason=%s",
                        int(elapsed * 1000),
                        self._current.value,
                        transition.reason or "-",
                    )
                    self._last_cycle_debug = now
                
                delay_s = max(0.0, transition.delay_ms / 1000.0)
                sleep_s = max(0.01, self._poll_interval + delay_s - elapsed)
                time.sleep(sleep_s)

            except Exception as e:
                _log.exception("Engine Failure: %s", e)
                time.sleep(1.0)
