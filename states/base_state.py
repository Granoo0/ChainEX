"""
states/base_state.py — Abstract base class for all FSM states.

FINITE STATE MACHINE DESIGN
─────────────────────────────
Each game "situation" (menu, battle, puzzle…) is its own State class.
States receive:
  • The captured screen frame (NumPy array)
  • Access to the WindowController (to send clicks/keys)
  • Access to the ImageRecognizer (to locate UI elements)
  • The shared config dict

A state's `run()` method:
  1. Inspects the frame for expected UI elements.
  2. Performs one or more actions.
  3. Returns a StateTransition indicating the next state (may be SELF if
     nothing needs to change) and an optional delay before the next cycle.

The BotEngine calls `run()` in a tight loop and follows the transitions.

WHY THIS DESIGN?
─────────────────
Separating logic into discrete states keeps each class small and testable.
Adding a new game screen means adding a new State subclass — no spaghetti
if/else chains in a monolithic loop.
"""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np
    from window_ctrl      import WindowController
    from image_recognizer import ImageRecognizer


# ─────────────────────────────────────────────────────────────────────────────
# State names — the FSM's vocabulary of game situations
# ─────────────────────────────────────────────────────────────────────────────
class StateName(enum.Enum):
    # ── Active states ────────────────────────────────────────────────────
    MAIN_MENU   = "main_menu"    # At the home screen
    MACRO       = "macro"        # Sequential template clicking

    # ── Error / recovery ────────────────────────────────────────────────
    ERROR       = "error"        # Unrecoverable error; request stop

    # ── Sentinel ─────────────────────────────────────────────────────────
    SELF        = "self"         # "Stay in this state" shorthand


# ─────────────────────────────────────────────────────────────────────────────
# Transition object returned by each State
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class StateTransition:
    """
    Encodes what the FSM engine should do after a state's run() completes.

    Attributes:
        next_state  : The state to transition to (StateName.SELF = no change).
        delay_ms    : Minimum milliseconds to wait before the next cycle.
        reason      : Human-readable description for the log.
    """
    next_state: StateName       = StateName.SELF
    delay_ms:   int             = 0
    reason:     str             = ""

    @classmethod
    def stay(cls, reason: str = "", delay_ms: int = 0) -> "StateTransition":
        """Convenience: remain in the current state."""
        return cls(StateName.SELF, delay_ms, reason)

    @classmethod
    def go(cls, state: StateName, reason: str = "",
           delay_ms: int = 0) -> "StateTransition":
        """Convenience: transition to a specific state."""
        return cls(state, delay_ms, reason)


# ─────────────────────────────────────────────────────────────────────────────
# Base state
# ─────────────────────────────────────────────────────────────────────────────
class BaseState(ABC):
    """
    All concrete states inherit from this class.

    Subclasses must implement `run()` and `name`.
    """

    def __init__(
        self,
        wc:  "WindowController",
        ir:  "ImageRecognizer",
        cfg: dict,
    ) -> None:
        self.wc   = wc
        self.ir   = ir
        self.cfg  = cfg

    @property
    @abstractmethod
    def name(self) -> StateName:
        """The StateName this class handles."""

    @abstractmethod
    def run(self, frame: "np.ndarray") -> StateTransition:
        """
        Inspect *frame*, perform actions, return a StateTransition.
        Must NOT block for long periods — keep individual actions short
        and return promptly so the engine can re-capture and re-evaluate.
        """

