"""
tests/test_states.py — Unit tests for StateName, StateTransition, and BaseState.

Run: python -m pytest tests/ -v
  or: python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from states.base_state import StateName, StateTransition  # noqa: E402


# ── StateName ──────────────────────────────────────────────────────────────────

class TestStateName(unittest.TestCase):

    # ── Live values must exist ───────────────────────────────────────────────

    def test_main_menu_value(self):
        self.assertEqual(StateName.MAIN_MENU.value, "main_menu")

    def test_macro_value(self):
        self.assertEqual(StateName.MACRO.value, "macro")

    def test_error_value(self):
        self.assertEqual(StateName.ERROR.value, "error")

    def test_self_value(self):
        self.assertEqual(StateName.SELF.value, "self")

    # ── Removed dead values must not exist ───────────────────────────────────

    def _assert_no_member(self, name: str) -> None:
        self.assertFalse(
            hasattr(StateName, name),
            f"StateName.{name} should have been removed (dead state).",
        )

    def test_mission_sel_removed(self):
        self._assert_no_member("MISSION_SEL")

    def test_loading_removed(self):
        self._assert_no_member("LOADING")

    def test_battle_removed(self):
        self._assert_no_member("BATTLE")

    def test_puzzle_removed(self):
        self._assert_no_member("PUZZLE")

    def test_reward_removed(self):
        self._assert_no_member("REWARD")

    def test_exam_removed(self):
        self._assert_no_member("EXAM")

    def test_unknown_removed(self):
        self._assert_no_member("UNKNOWN")

    def test_popup_removed(self):
        self._assert_no_member("POPUP")

    # ── Enum is exhaustive ───────────────────────────────────────────────────

    def test_exactly_four_members(self):
        """Prevent accidental additions without updating this test."""
        self.assertEqual(
            len(StateName),
            4,
            f"Expected exactly 4 StateName members, got {len(StateName)}: "
            f"{[m.name for m in StateName]}",
        )


# ── StateTransition ────────────────────────────────────────────────────────────

class TestStateTransition(unittest.TestCase):

    def test_default_next_state_is_self(self):
        t = StateTransition()
        self.assertEqual(t.next_state, StateName.SELF)

    def test_default_delay_is_zero(self):
        self.assertEqual(StateTransition().delay_ms, 0)

    def test_default_reason_is_empty(self):
        self.assertEqual(StateTransition().reason, "")

    def test_stay_returns_self_state(self):
        t = StateTransition.stay(reason="test", delay_ms=500)
        self.assertEqual(t.next_state, StateName.SELF)
        self.assertEqual(t.reason, "test")
        self.assertEqual(t.delay_ms, 500)

    def test_stay_no_args(self):
        t = StateTransition.stay()
        self.assertEqual(t.next_state, StateName.SELF)
        self.assertEqual(t.delay_ms, 0)
        self.assertEqual(t.reason, "")

    def test_go_sets_correct_state(self):
        t = StateTransition.go(StateName.MACRO, reason="starting", delay_ms=100)
        self.assertEqual(t.next_state, StateName.MACRO)
        self.assertEqual(t.reason, "starting")
        self.assertEqual(t.delay_ms, 100)

    def test_go_error_state(self):
        t = StateTransition.go(StateName.ERROR, reason="fatal")
        self.assertEqual(t.next_state, StateName.ERROR)
        self.assertEqual(t.reason, "fatal")

    def test_go_main_menu(self):
        t = StateTransition.go(StateName.MAIN_MENU, reason="done")
        self.assertEqual(t.next_state, StateName.MAIN_MENU)

    def test_transition_equality(self):
        a = StateTransition(StateName.MACRO, 200, "go")
        b = StateTransition(StateName.MACRO, 200, "go")
        self.assertEqual(a, b)

    def test_transition_inequality(self):
        a = StateTransition(StateName.MACRO, 200, "go")
        b = StateTransition(StateName.ERROR, 200, "go")
        self.assertNotEqual(a, b)


if __name__ == "__main__":
    unittest.main()
