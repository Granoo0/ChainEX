"""
tests/test_macro_state.py — Unit tests for MacroState (the macro execution engine).

All Windows API calls are mocked — these tests run without a live game window.

Run: python -m pytest tests/ -v
  or: python -m unittest discover -s tests -v
"""

from __future__ import annotations

import queue
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from states.macro_state import MacroState          # noqa: E402
from states.base_state  import StateName, StateTransition  # noqa: E402

# A blank frame that satisfies all visual checks without triggering real matching.
_FRAME = np.zeros((200, 200, 3), dtype=np.uint8)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_state(
    sequence,
    *,
    repeat: bool = True,
    cfg_extra: dict | None = None,
    stats_queue=None,
    pause_event=None,
    stop_after_loop=None,
) -> tuple[MacroState, MagicMock, MagicMock]:
    """
    Construct a MacroState with mock WindowController and ImageRecognizer.

    ir.find() returns None by default (template not found).
    Override per-test with  ir.find.return_value = (name, conf, bbox).
    """
    wc = MagicMock()
    ir = MagicMock()
    ir.find.return_value = None

    cfg: dict = {
        "macro": {
            "sequence":         list(sequence),
            "repeat":           repeat,
            "wait_timeout_s":   5.0,
            "click_delay_ms":   100,
            "playback_speed":   1.0,
            "max_click_retries": 3,
            "interrupts":       [],
        },
        "input": {
            "focus_before_actions":   False,
            "force_global_click":     False,
            "random_click_offset_px": 0,
        },
    }
    if cfg_extra:
        _merge(cfg, cfg_extra)

    state = MacroState(
        wc, ir, cfg,
        stats_queue=stats_queue,
        pause_event=pause_event,
        stop_after_loop=stop_after_loop,
    )
    return state, wc, ir


def _merge(base: dict, override: dict) -> None:
    """Recursive in-place merge (override wins on conflict)."""
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _merge(base[k], v)
        else:
            base[k] = v


# ── Pause gate ─────────────────────────────────────────────────────────────────

class TestPauseGate(unittest.TestCase):

    def test_paused_returns_stay_with_reason(self):
        pause = threading.Event()
        pause.set()
        state, _, _ = _make_state(["btn_ok"], pause_event=pause)
        result = state.run(_FRAME)
        self.assertEqual(result.next_state, StateName.SELF)
        self.assertEqual(result.reason, "paused")

    def test_not_paused_does_not_gate(self):
        pause = threading.Event()  # not set
        state, _, ir = _make_state(["POS:10,20"], pause_event=pause)
        result = state.run(None)
        self.assertNotEqual(result.reason, "paused")


# ── Empty sequence ─────────────────────────────────────────────────────────────

class TestEmptySequence(unittest.TestCase):

    def test_empty_sequence_returns_main_menu(self):
        state, _, _ = _make_state([])
        result = state.run(_FRAME)
        self.assertEqual(result.next_state, StateName.MAIN_MENU)


# ── POS: command ───────────────────────────────────────────────────────────────

class TestPosCommand(unittest.TestCase):

    def test_pos_local_click(self):
        state, wc, _ = _make_state(["POS:50,60"])
        state.run(None)
        wc.click.assert_called_once_with(50, 60)
        self.assertEqual(state._index, 1)

    def test_pos_global_flag_uses_click_global(self):
        state, wc, _ = _make_state(["POS:50,60,GLOBAL"])
        state.run(None)
        wc.click_global.assert_called_once_with(50, 60)
        wc.click.assert_not_called()

    def test_pos_force_global_config_overrides(self):
        state, wc, _ = _make_state(
            ["POS:50,60"],
            cfg_extra={"input": {"force_global_click": True}},
        )
        state.run(None)
        wc.click_global.assert_called_once_with(50, 60)

    def test_pos_invalid_format_does_not_crash(self):
        """A malformed POS: value must not raise — step advances anyway."""
        state, wc, _ = _make_state(["POS:bad,data"])
        result = state.run(None)
        self.assertEqual(state._index, 1)

    def test_pos_returns_delay(self):
        state, _, _ = _make_state(["POS:1,1"])
        result = state.run(None)
        self.assertGreater(result.delay_ms, 0)


# ── WAIT: command ──────────────────────────────────────────────────────────────

class TestWaitCommand(unittest.TestCase):

    def test_wait_advances_index(self):
        state, _, _ = _make_state(["WAIT:2.0"])
        state.run(None)
        self.assertEqual(state._index, 1)

    def test_wait_returns_delay_in_ms(self):
        state, _, _ = _make_state(["WAIT:2.0"])
        result = state.run(None)
        self.assertAlmostEqual(result.delay_ms, 2000, delta=50)

    def test_wait_scaled_by_playback_speed(self):
        state, _, _ = _make_state(
            ["WAIT:2.0"],
            cfg_extra={"macro": {"playback_speed": 2.0}},
        )
        result = state.run(None)
        # At 2× speed, 2s wait → 1s
        self.assertAlmostEqual(result.delay_ms, 1000, delta=50)

    def test_wait_bad_value_does_not_crash(self):
        state, _, _ = _make_state(["WAIT:notanumber"])
        result = state.run(None)
        self.assertEqual(state._index, 1)


# ── KEY: command ───────────────────────────────────────────────────────────────

class TestKeyCommand(unittest.TestCase):

    def _vk(self):
        import win32con
        return win32con

    def test_key_space(self):
        state, wc, _ = _make_state(["KEY:SPACE"])
        state.run(None)
        wc.key_press.assert_called_once_with(self._vk().VK_SPACE)
        self.assertEqual(state._index, 1)

    def test_key_enter(self):
        state, wc, _ = _make_state(["KEY:ENTER"])
        state.run(None)
        wc.key_press.assert_called_once_with(self._vk().VK_RETURN)

    def test_key_escape(self):
        state, wc, _ = _make_state(["KEY:ESC"])
        state.run(None)
        wc.key_press.assert_called_once_with(self._vk().VK_ESCAPE)

    def test_key_single_char(self):
        state, wc, _ = _make_state(["KEY:A"])
        state.run(None)
        wc.key_press.assert_called_once_with(ord("A"))

    def test_key_focus_before_actions(self):
        state, wc, _ = _make_state(
            ["KEY:SPACE"],
            cfg_extra={"input": {"focus_before_actions": True}},
        )
        state.run(None)
        wc.bring_to_foreground.assert_called_once()


# ── Template click & _pending_verify FSM ──────────────────────────────────────

class TestTemplateClick(unittest.TestCase):

    _MATCH = ("btn_ok", 0.95, (10, 10, 20, 20))

    def test_template_not_found_stays_at_same_index(self):
        state, _, ir = _make_state(["btn_ok"])
        ir.find.return_value = None
        result = state.run(_FRAME)
        self.assertEqual(result.next_state, StateName.SELF)
        self.assertEqual(state._index, 0)

    def test_template_found_arms_pending_verify(self):
        state, wc, ir = _make_state(["btn_ok"])
        ir.find.return_value = self._MATCH
        state.run(_FRAME)
        self.assertIsNotNone(state._pending_verify)
        pi, pstem, retries, _ = state._pending_verify
        self.assertEqual(pi, 0)
        self.assertEqual(pstem, "btn_ok")
        self.assertEqual(retries, 0)

    def test_template_found_triggers_global_click_by_default(self):
        state, wc, ir = _make_state(["btn_ok"])
        ir.find.return_value = self._MATCH
        state.run(_FRAME)
        wc.click_global.assert_called_once()
        wc.click.assert_not_called()

    def test_pending_verify_advances_when_template_gone(self):
        state, _, ir = _make_state(["btn_ok", "btn_next"])
        ir.find.return_value = self._MATCH
        state.run(_FRAME)                   # click → pending_verify set
        self.assertEqual(state._index, 0)
        ir.find.return_value = None         # template gone
        state.run(_FRAME)                   # confirm → advance
        self.assertEqual(state._index, 1)
        self.assertIsNone(state._pending_verify)

    def test_pending_verify_retries_when_template_still_visible(self):
        state, wc, ir = _make_state(["btn_ok"])
        ir.find.return_value = self._MATCH
        state.run(_FRAME)                   # initial click, retries=0
        wc.click_global.reset_mock()
        state.run(_FRAME)                   # template still here → retry #1
        _, _, retries, _ = state._pending_verify
        self.assertEqual(retries, 1)
        wc.click_global.assert_called_once()

    def test_pending_verify_advances_after_max_retries(self):
        """After max_click_retries retries engine advances despite template still visible."""
        state, _, ir = _make_state(["btn_ok", "btn_next"])
        ir.find.return_value = self._MATCH
        state.run(_FRAME)                     # initial click (retries=0)
        for _ in range(3):                    # retries become 1, 2, 3
            state.run(_FRAME)
        self.assertEqual(state._index, 0)
        _, _, retries, _ = state._pending_verify
        self.assertEqual(retries, 3)
        state.run(_FRAME)                     # retries=3 >= max_r=3 → advance
        self.assertEqual(state._index, 1)
        self.assertIsNone(state._pending_verify)

    def test_click_mode_local_uses_postmessage(self):
        step = {"template": "btn_ok", "click_mode": "local"}
        state, wc, ir = _make_state([step])
        ir.find.return_value = self._MATCH
        state.run(_FRAME)
        wc.click.assert_called_once()
        wc.click_global.assert_not_called()

    def test_empty_template_name_is_skipped(self):
        state, _, _ = _make_state([{"template": "", "label": "broken"}])
        result = state.run(_FRAME)
        self.assertEqual(state._index, 1)

    def test_template_with_png_extension_normalised(self):
        """Passing 'btn_ok.png' as a template name must be normalised to 'btn_ok'."""
        state, wc, ir = _make_state(["btn_ok.png"])
        ir.find.return_value = ("btn_ok", 0.95, (10, 10, 20, 20))
        state.run(_FRAME)
        # ir.find should have been called with the normalized name
        called_name = ir.find.call_args[0][1]
        self.assertEqual(called_name, "btn_ok")


# ── Disabled steps ─────────────────────────────────────────────────────────────

class TestDisabledSteps(unittest.TestCase):

    def test_disabled_step_skipped(self):
        step = {"template": "btn_ok", "enabled": False}
        state, _, ir = _make_state([step, "btn_next"])
        ir.find.return_value = ("btn_ok", 0.95, (10, 10, 20, 20))
        state.run(_FRAME)
        self.assertEqual(state._index, 1)

    def test_enabled_step_executed(self):
        step = {"template": "btn_ok", "enabled": True}
        state, _, ir = _make_state([step])
        ir.find.return_value = ("btn_ok", 0.95, (10, 10, 20, 20))
        state.run(_FRAME)
        self.assertIsNotNone(state._pending_verify)

    def test_missing_enabled_key_defaults_to_true(self):
        step = {"template": "btn_ok"}  # no "enabled" key
        state, _, ir = _make_state([step])
        ir.find.return_value = ("btn_ok", 0.95, (10, 10, 20, 20))
        state.run(_FRAME)
        self.assertIsNotNone(state._pending_verify)


# ── Loop behaviour ─────────────────────────────────────────────────────────────

class TestLoopBehaviour(unittest.TestCase):

    def test_repeat_true_wraps_index_to_zero(self):
        state, _, _ = _make_state(["btn_ok"], repeat=True)
        state._index = 1              # past the end of a 1-step sequence
        state.run(_FRAME)
        self.assertEqual(state._index, 0)

    def test_repeat_false_returns_macro_complete(self):
        state, _, _ = _make_state(["btn_ok"], repeat=False)
        state._index = 1
        result = state.run(_FRAME)
        self.assertEqual(result.next_state, StateName.MAIN_MENU)
        self.assertEqual(result.reason, "Macro complete")

    def test_stop_after_loop_signals_secondary_switch(self):
        flag = threading.Event()
        flag.set()
        state, _, _ = _make_state(["btn_ok"], stop_after_loop=flag)
        state._index = 1              # trigger end-of-sequence
        result = state.run(_FRAME)
        self.assertEqual(result.next_state, StateName.MAIN_MENU)
        self.assertEqual(result.reason, "secondary_switch")


# ── Stats queue ────────────────────────────────────────────────────────────────

class TestStatsQueue(unittest.TestCase):

    def test_stats_pushed_on_loop_complete(self):
        q = queue.Queue(maxsize=1)
        state, _, _ = _make_state(["btn_ok"], repeat=True, stats_queue=q)
        state._index = 1              # simulate end-of-loop
        state.run(_FRAME)
        self.assertFalse(q.empty())
        data = q.get_nowait()
        self.assertIn("loops",   data)
        self.assertIn("loop_ms", data)
        self.assertIn("avg_ms",  data)
        self.assertEqual(data["loops"], 1)

    def test_stats_loop_counter_increments(self):
        q = queue.Queue(maxsize=1)
        state, _, _ = _make_state(["btn_ok"], repeat=True, stats_queue=q)
        for _ in range(3):
            state._index = 1
            state.run(_FRAME)
        data = q.get_nowait()
        self.assertEqual(data["loops"], 3)


# ── _last_interrupt_check initialisation ──────────────────────────────────────

class TestInterruptCheckInit(unittest.TestCase):

    def test_attribute_exists_after_construction(self):
        """_last_interrupt_check must be set in __init__ (not via getattr fallback)."""
        state, _, _ = _make_state(["btn_ok"])
        self.assertTrue(
            hasattr(state, "_last_interrupt_check"),
            "_last_interrupt_check should be initialised in _load_macro_config",
        )

    def test_attribute_initialised_to_zero(self):
        state, _, _ = _make_state(["btn_ok"])
        self.assertEqual(state._last_interrupt_check, 0.0)

    def test_no_attribute_error_with_active_interrupts(self):
        """Interrupt polling must not crash on the very first run() call."""
        interrupt = {"if_visible": "some_popup", "trigger_step": 1}
        state, _, ir = _make_state(
            ["btn_ok"],
            cfg_extra={"macro": {"interrupts": [interrupt]}},
        )
        ir.find.return_value = None
        try:
            state.run(_FRAME)
        except AttributeError as exc:
            self.fail(f"AttributeError on first run with interrupts: {exc}")


if __name__ == "__main__":
    unittest.main()
