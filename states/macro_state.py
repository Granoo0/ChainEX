import queue as _queue_mod
import threading
import time
import logging
import os
import random
from typing import Any, Optional, Tuple

import numpy as np
import win32con

from states.base_state import BaseState, StateName, StateTransition

_log = logging.getLogger("G Panel.Macro")


class MacroState(BaseState):
    """
    State for sequential macro execution with conditional logic.
    Supports a list of templates or dicts with 'if_visible' logic.
    Optional two-phase click: local PostMessage first, then global if UI unchanged.
    """

    def __init__(self, *args, **kwargs) -> None:
        # Pop before super() — BaseState doesn't accept extra kwargs
        self._stop_after_loop: Optional[threading.Event] = kwargs.pop("stop_after_loop", None)
        self._stats_queue: Optional[_queue_mod.Queue]    = kwargs.pop("stats_queue",     None)
        self._pause_event: Optional[threading.Event]     = kwargs.pop("pause_event",     None)
        self._active_step_ref: Optional[list]            = kwargs.pop("step_ref",        None)
        super().__init__(*args, **kwargs)
        self._index = 0
        self._sequence = []
        self._repeat = True
        self._timeout = 5.0
        self._click_delay_ms = 800
        self._interrupts = []
        self._wait_start: float | None = None
        self._load_macro_config()
        # (step_index, template_stem, retry_count, jump_target)
        # Set after every template click; cleared when template disappears or retries exhausted.
        self._pending_verify: Optional[Tuple[int, str, int, Optional[int]]] = None

    def _load_macro_config(self):
        macro_cfg = self.cfg.get("macro", {})
        self._sequence = macro_cfg.get("sequence", [])
        self._repeat = macro_cfg.get("repeat", True)
        self._timeout = macro_cfg.get("wait_timeout_s", 5.0)
        
        self._playback_speed = float(macro_cfg.get("playback_speed", 1.0))
        # Scale the base click delay by the inverse of speed (2x speed = 0.5x delay)
        self._click_delay_ms = int(float(macro_cfg.get("click_delay_ms", 800)) / self._playback_speed)
        
        self._interrupts = macro_cfg.get("interrupts", [])
        self._index = 0
        self._wait_start = None
        self._pending_verify = None
        self._step_cycles_done = 0
        self._step_last_executed = {}
        self._interrupt_end_time = None
        self._last_interrupt_check = 0.0
        self._backup_sequence = None
        self._backup_index = 0
        self._backup_repeat = False
        
        # Sub-Loop Runtime Stats
        self._main_cycle_done = 0
        self._sub_cycle_done = 0
        self._is_in_subloop = False
        self._main_sequence_ref = None
        self._main_index_backup = 0

        # Loop timing (for stats)
        self._loop_start_time: float = time.monotonic()
        self._loop_durations:  list  = []   # rolling window (last 20)

    def needs_frame(self) -> bool:
        """
        Optimization: Returns True if the current macro logic needs a new
        screen capture (e.g. for image recognition or global interrupts).
        """
        # 1. Global Interrupts check (every 250ms)
        if self._interrupts and time.monotonic() - self._last_interrupt_check > 0.25:
            return True
            
        # 2. Check if current step or pending verify needs visual searching
        if self._pending_verify is not None:
            return True
            
        if self._index < len(self._sequence):
            item = self._sequence[self._index]
            # If it's a string, check if it's a template (not POS/WAIT/KEY)
            if isinstance(item, str):
                cmd = item.upper().strip()
                if not cmd.startswith(("POS:", "WAIT:", "KEY:")):
                    return True
            # If it's a dict, check if it has visual conditions.
            # Must apply the same POS/WAIT/KEY filter as the string branch —
            # a step stored as {"template": "POS:100,200", "label": "..."} is
            # still a direct command and does NOT need a frame capture.
            elif isinstance(item, dict):
                if item.get("if_visible"):
                    return True
                tpl_raw = (item.get("template") or "").upper().strip()
                if tpl_raw and not tpl_raw.startswith(("POS:", "WAIT:", "KEY:")):
                    return True
                    
        return False

    def run(self, frame: Optional[np.ndarray]) -> StateTransition:
        # ── Pause gate ───────────────────────────────────────────────────
        if self._pause_event and self._pause_event.is_set():
            return StateTransition.stay(reason="paused", delay_ms=100)

        if not self._sequence and self._interrupt_end_time is None:
            return StateTransition.go(StateName.MAIN_MENU, reason="Macro sequence empty")

        # Dynamic Speed & Timing
        macro_cfg = self.cfg.get("macro", {})
        self._playback_speed = max(0.1, float(macro_cfg.get("playback_speed", 1.0)))
        cd_base = float(macro_cfg.get("click_delay_ms", 800))
        current_click_delay = int(cd_base / self._playback_speed)

        if self._interrupt_end_time is not None:
            if time.monotonic() >= self._interrupt_end_time or self._index >= len(self._sequence):
                _log.info("Interrupt finished, returning to step %d", self._backup_index + 1)
                self._sequence = self._backup_sequence
                self._index = self._backup_index
                self._repeat = self._backup_repeat
                self._interrupt_end_time = None
                self._wait_start = None
                self._step_cycles_done = 0
                self._pending_verify = None

        if self._interrupt_end_time is None and self._interrupts and time.monotonic() - self._last_interrupt_check > 0.25:
            if frame is None: return StateTransition.stay() # Missing frame for interrupt check
            self._last_interrupt_check = time.monotonic()
            for inter in self._interrupts:
                tpl = inter.get("if_visible")
                if tpl:
                    match = self.ir.find(frame, self._normalize_template_name(tpl))
                    if match:
                        _log.info("Global Trigger '%s' met. Interrupting!", tpl)
                        dur = float(inter.get("duration_s", 5.0))
                        
                        if "execute_templates" in inter and inter["execute_templates"]:
                            # Option B: replace sequence with the interrupt templates
                            self._backup_sequence = self._sequence
                            self._backup_index = self._index
                            self._backup_repeat = self._repeat
                            self._sequence = inter["execute_templates"]
                            self._index = 0
                            self._repeat = False
                            self._interrupt_end_time = time.monotonic() + dur
                        else:
                            # Option A: jump within the current sequence
                            jump = int(inter.get("trigger_step", 1)) - 1
                            if 0 <= jump < len(self._sequence):
                                self._backup_sequence = self._sequence
                                self._backup_index = self._index
                                self._backup_repeat = self._repeat
                                self._index = jump
                                self._interrupt_end_time = time.monotonic() + dur
                            else:
                                _log.warning(
                                    "Interrupt trigger_step %d is out of range (seq len=%d) — skipped",
                                    jump + 1, len(self._sequence)
                                )
                        
                        self._wait_start = None
                        self._step_cycles_done = 0
                        self._pending_verify = None
                        return StateTransition.stay(delay_ms=250)

        if self._index >= len(self._sequence):
            # End of current sequence reached - check for sub-loop transitions
            sl = self.cfg.get("macro", {}).get("sub_loop", {})
            
            if self._is_in_subloop:
                self._sub_cycle_done += 1
                if self._sub_cycle_done >= int(sl.get("run_for", 2)):
                    # Switch back to MAIN
                    _log.info("Sub-Loop complete (%d cycles). Returning to Main Macro.", self._sub_cycle_done)
                    self._is_in_subloop = False
                    self._sequence = self._main_sequence_ref
                    self._index = 0
                    self._sub_cycle_done = 0
                    self._main_cycle_done = 0 # Reset main counter for next trigger
                else:
                    self._index = 0
            else:
                self._main_cycle_done += 1

                # ── Loop timing & stats ──────────────────────────────────
                now = time.monotonic()
                loop_ms = int((now - self._loop_start_time) * 1000)
                self._loop_durations.append(loop_ms)
                if len(self._loop_durations) > 20:
                    self._loop_durations.pop(0)
                avg_ms = int(sum(self._loop_durations) / len(self._loop_durations))
                self._loop_start_time = now
                if self._stats_queue is not None:
                    try:
                        # Drain any stale entry with an unconditional loop so we
                        # never hit the TOCTOU race of  "empty() → get_nowait()".
                        while True:
                            self._stats_queue.get_nowait()
                    except _queue_mod.Empty:
                        pass
                    try:
                        self._stats_queue.put_nowait({
                            "loops":   self._main_cycle_done,
                            "loop_ms": loop_ms,
                            "avg_ms":  avg_ms,
                        })
                    except _queue_mod.Full:
                        pass

                # ── Secondary-macro switch: stop cleanly after a full loop ──
                if self._stop_after_loop and self._stop_after_loop.is_set():
                    _log.info("Stop-after-loop triggered — main cycle %d complete. Handing off.",
                              self._main_cycle_done)
                    return StateTransition.go(StateName.MAIN_MENU, reason="secondary_switch")

                should_trigger = (sl.get("enabled", False) and
                                 sl.get("sequence") and 
                                 self._main_cycle_done >= int(sl.get("trigger_every", 10)))
                
                if should_trigger:
                    # Switch to SUB-LOOP
                    _log.info("Main Macro completed %d cycles. Switching to Sub-Loop for %s cycles.", self._main_cycle_done, sl.get("run_for"))
                    self._is_in_subloop = True
                    self._main_sequence_ref = self._sequence
                    self._sequence = sl.get("sequence")
                    self._index = 0
                    self._sub_cycle_done = 0
                elif self._repeat:
                    self._index = 0
                else:
                    return StateTransition.go(StateName.MAIN_MENU, reason="Macro complete")

        def _advance_or_repeat(jump_target):
            self._wait_start = None
            self._pending_verify = None
            if jump_target and jump_target > 0:
                self._index = (jump_target - 1) % len(self._sequence)
                self._step_cycles_done = 0
            else:
                # Use self._sequence[self._index] instead of the local `current_item`
                # variable, which may not yet be defined when this helper is called
                # from the _pending_verify block (before line 272 is reached).
                _ci = (self._sequence[self._index]
                       if self._index < len(self._sequence) else {})
                needed = _ci.get("cycle_count", 1) if isinstance(_ci, dict) else 1
                self._step_cycles_done += 1
                if self._step_cycles_done >= needed:
                    self._next_step()

        # ── Post-click confirmation: wait for the template to disappear ──────
        # Every template click sets _pending_verify. We stay on the same step
        # until the template is gone (confirming the click registered) or until
        # max_click_retries global retries are exhausted.
        if self._pending_verify is not None:
            pi, pstem, retries, jump_then = self._pending_verify
            if self._index != pi:
                # Index changed externally (interrupt etc.) — clear and continue
                self._pending_verify = None
            else:
                if frame is None:
                    return StateTransition.stay()

                still_here = self.ir.find(frame, pstem) is not None if pstem else False
                max_r = int(self.cfg.get("macro", {}).get("max_click_retries", 5))

                if still_here and retries < max_r:
                    # Template still on screen — fire a global click and wait again
                    m2 = self.ir.find(frame, pstem)
                    if m2:
                        _, _, bb2 = m2
                        rand_px = int(self.cfg.get("input", {}).get("random_click_offset_px", 0))
                        hx = hy = 0
                        if rand_px > 0:
                            hx = random.randint(-min(rand_px, max(1, bb2[2]//2)),
                                                 min(rand_px, max(1, bb2[2]//2)))
                            hy = random.randint(-min(rand_px, max(1, bb2[3]//2)),
                                                 min(rand_px, max(1, bb2[3]//2)))
                        rx = bb2[0] + bb2[2] // 2 + hx
                        ry = bb2[1] + bb2[3] // 2 + hy
                        self.wc.click_global(rx, ry)
                        _log.info(
                            "Macro Step %d: '%s' still visible — global retry %d/%d at (%d,%d)",
                            pi + 1, pstem, retries + 1, max_r, rx, ry,
                        )
                    self._pending_verify = (pi, pstem, retries + 1, jump_then)
                    return StateTransition.stay(delay_ms=current_click_delay)

                # Template gone  OR  retries exhausted — advance to next step
                if still_here:
                    _log.warning(
                        "Macro Step %d: '%s' still visible after %d retries — advancing anyway",
                        pi + 1, pstem, max_r,
                    )
                else:
                    _log.info("Macro Step %d: '%s' gone — confirmed, advancing", pi + 1, pstem)

                self._pending_verify = None
                self._wait_start = None
                _advance_or_repeat(jump_then)
                return StateTransition.stay(delay_ms=current_click_delay)

        current_item = self._sequence[self._index]
        template_name = current_item if isinstance(current_item, str) else current_item.get("template", "")

        # Broadcast current step index to UI highlight bar (GIL-safe single write)
        if self._active_step_ref is not None:
            self._active_step_ref[0] = self._index

        # Skip disabled steps
        if isinstance(current_item, dict) and not current_item.get("enabled", True):
            _log.info(
                "Macro Step %d: disabled — skipping ('%s')",
                self._index + 1,
                current_item.get("label") or current_item.get("template") or "?",
            )
            self._next_step()
            return StateTransition.stay(delay_ms=50)

        # Cooldown Check
        cd_s = float(current_item.get("cooldown_s", 0) if isinstance(current_item, dict) else 0)
        if cd_s > 0 and self._step_cycles_done == 0:
            last_exec = self._step_last_executed.get(self._index, 0.0)
            if time.monotonic() - last_exec < cd_s:
                _log.debug("Macro Step %d: Cooldown active, skipping", self._index + 1)
                self._next_step()
                return StateTransition.stay()

        # Keyboard & Wait Commands
        cmd_upper = str(template_name).upper().strip()
        if cmd_upper.startswith("KEY:"):
            key_val = cmd_upper[4:].strip()
            vk = 0
            if key_val == "SPACE": vk = win32con.VK_SPACE
            elif key_val == "ENTER": vk = win32con.VK_RETURN
            elif key_val in ("ESC", "ESCAPE"): vk = win32con.VK_ESCAPE
            elif len(key_val) == 1: vk = ord(key_val)
            
            if vk > 0:
                if self.cfg.get("input", {}).get("focus_before_actions"):
                    self.wc.bring_to_foreground()
                self.wc.key_press(vk)
                _log.info("Macro Step %d: Pressed Key %s", self._index + 1, key_val)
            self._step_last_executed[self._index] = time.monotonic()
            _advance_or_repeat(current_item.get("then_jump", 0) if isinstance(current_item, dict) else 0)
            return StateTransition.stay(delay_ms=current_click_delay)
            
        elif cmd_upper.startswith("POS:"):
            try:
                # Format: POS:123,456 or POS:123,456,GLOBAL
                parts = cmd_upper[4:].split(",")
                px, py = int(parts[0]), int(parts[1])
                is_global = len(parts) > 2 and parts[2].strip() == "GLOBAL"
                
                if self.cfg.get("input", {}).get("focus_before_actions"):
                    self.wc.bring_to_foreground()

                msg = f"Macro Step {self._index + 1}: Click Position ({px}, {py})"
                if is_global or self.cfg.get("input", {}).get("force_global_click"):
                    _log.info(msg + " [GLOBAL MODE]")
                    self.wc.click_global(px, py)
                else:
                    _log.info(msg + " [LOCAL MODE]")
                    self.wc.click(px, py)
            except Exception as e:
                _log.error("Failed to parse POS command at Step %d: %s", self._index + 1, e)
                
            self._step_last_executed[self._index] = time.monotonic()
            _advance_or_repeat(current_item.get("then_jump", 0) if isinstance(current_item, dict) else 0)
            return StateTransition.stay(delay_ms=current_click_delay)
            
        elif cmd_upper.startswith("WAIT:"):
            try:
                # Scale the wait time by the playback speed multiplier
                w_sec = float(cmd_upper[5:].strip()) / self._playback_speed
            except ValueError:
                w_sec = 1.0 / self._playback_speed
            _log.info("Macro Step %d: Waiting %.2fs", self._index + 1, w_sec)
            self._step_last_executed[self._index] = time.monotonic()
            _advance_or_repeat(current_item.get("then_jump", 0) if isinstance(current_item, dict) else 0)
            return StateTransition.stay(delay_ms=int(w_sec * 1000.0))

        # Advanced Logic (IF/THEN)
        jump_target = None
        if isinstance(current_item, dict):
            if current_item.get("if_visible"):
                # Need a live frame to evaluate the condition
                if frame is None:
                    return StateTransition.stay()
                cond_name = current_item["if_visible"]
                if self.ir.find(frame, self._normalize_template_name(cond_name)) is not None:
                    jump_target = current_item.get("then_jump", 0)
                    _log.info("Condition '%s' met! Jump to %s", cond_name, jump_target)
                else:
                    self._next_step()
                    return StateTransition.stay()
            else:
                jump_target = current_item.get("then_jump", 0)

        # Action: Click
        template_name = self._normalize_template_name(template_name)
        if not template_name:
            _log.warning(
                "Macro Step %d: empty template name — skipping step.",
                self._index + 1,
            )
            self._next_step()
            return StateTransition.stay()

        if frame is None: return StateTransition.stay()
        match = self.ir.find(frame, template_name)
        if match:
            self._step_last_executed[self._index] = time.monotonic()
            _, _, bbox = match
            
            # Apply Humanized Random Offset
            rand_px = int(self.cfg.get("input", {}).get("random_click_offset_px", 0))
            hx = hy = 0
            if rand_px > 0:
                hx = random.randint(-min(rand_px, max(1, bbox[2]//2)), min(rand_px, max(1, bbox[2]//2)))
                hy = random.randint(-min(rand_px, max(1, bbox[3]//2)), min(rand_px, max(1, bbox[3]//2)))
                
            cx = bbox[0] + bbox[2] // 2 + hx
            cy = bbox[1] + bbox[3] // 2 + hy
            
            # Click Logic — global by default for reliable game interaction.
            # Per-step "click_mode" key overrides; force_global_click config
            # overrides both.  After clicking we NEVER advance immediately —
            # instead we set _pending_verify so the handler at the top of run()
            # waits until the template disappears before calling _next_step().
            click_mode = "global"
            if isinstance(current_item, dict):
                click_mode = current_item.get("click_mode", "global")
            if self.cfg.get("input", {}).get("force_global_click"):
                click_mode = "global"

            if self.cfg.get("input", {}).get("focus_before_actions"):
                self.wc.bring_to_foreground()

            if click_mode == "global":
                self.wc.click_global(cx, cy)
                _log.info(
                    "Macro Step %d: Found '%s' → global click (%d, %d)",
                    self._index + 1, template_name, cx, cy,
                )
            else:
                self.wc.click(cx, cy)
                _log.info(
                    "Macro Step %d: Found '%s' → local click (%d, %d)",
                    self._index + 1, template_name, cx, cy,
                )

            # Arm confirmation check — advance only after template is gone
            self._wait_start = None
            self._pending_verify = (self._index, template_name, 0, jump_target)
            return StateTransition.stay(delay_ms=self._click_delay_ms)

        # Template not found yet — start or continue the wait timer.
        # We NEVER skip a template step on timeout; instead we log a warning
        # every _timeout seconds and keep retrying until the match appears.
        if self._wait_start is None:
            self._wait_start = time.monotonic()

        elapsed = time.monotonic() - self._wait_start
        if elapsed > self._timeout:
            _log.warning(
                "Macro Step %d: '%s' not visible after %.0fs — still waiting…",
                self._index + 1, template_name, elapsed,
            )
            # Reset so the next warning fires _timeout seconds from now,
            # not every single frame.
            self._wait_start = time.monotonic()

        return StateTransition.stay()

    def _next_step(self):
        """Advance to the next step.  Do NOT wrap here — run() detects
        index >= len(sequence) every cycle and handles repeat, sub-loops,
        stats and stop-after-loop in one place.

        Always resets _wait_start so that a disabled/skipped step does not
        bleed its stale timer into the next template-matching step.
        """
        self._index += 1
        self._step_cycles_done = 0
        self._wait_start = None

    def _normalize_template_name(self, value: Any) -> str | None:
        if not value:
            return None
        if not isinstance(value, str):
            value = str(value)
        # Accept "foo.png" in config even though recognizer keys are stem names.
        stem, _ext = os.path.splitext(value.strip())
        return stem.lower() if stem else None

    @property
    def name(self) -> StateName:
        return StateName.MACRO
