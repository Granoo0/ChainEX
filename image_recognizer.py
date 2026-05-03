"""
image_recognizer.py — OpenCV template-matching engine.

WHY TEMPLATE MATCHING?
──────────────────────
2D browser/Flash RPGs like Ninja Saga render fixed-size UI sprites (buttons,
icons, health bars) that are identical across sessions.  cv2.matchTemplate
with TM_CCOEFF_NORMED gives a confidence score in [0, 1] for how well a
small "template" image appears inside a larger "scene" image.  We accept
matches above a configurable threshold (default 0.80).

PERFORMANCE OPTIMISATIONS
──────────────────────────
1. Grayscale matching — converts both scene and template to single-channel
   before matching; halves pixel data and speeds up correlation by ~3×.
2. Region-of-interest (ROI) — caller can pass a (x, y, w, h) sub-region so
   only a fraction of the full frame is searched.
3. Template caching — templates are loaded from disk once and kept in memory.
4. Early-exit threshold — stop after finding the first match above threshold
   when we only need to know *if* something exists (find_any).

RESOLUTION INDEPENDENCE
────────────────────────
If the game runs at a resolution different from the one used when capturing
templates, scale the scene image to match the template capture resolution
before matching, or capture templates at multiple scales and try each.
The `multi_scale_find` helper demonstrates the latter approach.
"""

import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

from bot_logger import get_logger

_log = get_logger("ImageRec")

# Type alias: a match result = (template_name, confidence, (x, y, w, h))
MatchResult = Tuple[str, float, Tuple[int, int, int, int]]


class ImageRecognizer:
    """
    Loads template images from a directory and provides methods to locate
    them within a captured game frame.

    Template naming convention:
        templates/
            btn_start_mission.png
            btn_ok.png
            hpbar_empty.png
            enemy_present.png
            puzzle_scale.png
            reward_popup.png
            ...

    Each PNG should be a tightly cropped screenshot of the UI element you
    want to detect — ideally 30–200 px wide.  Smaller templates are faster.
    """

    def __init__(
        self,
        template_dir: str = "templates",
        threshold: float = 0.80,
        use_grayscale: bool = True,
        templates_meta_file: Optional[str] = None,
    ) -> None:
        self.threshold    = threshold
        self.use_grayscale = use_grayscale
        self._templates: Dict[str, np.ndarray] = {}
        self._roi_by_template: Dict[str, Tuple[int, int, int, int]] = {}
        self._missing_template_logged: set[str] = set()
        # Scene cache: keep a *strong reference* to the last scene so that
        # Python's allocator cannot reuse its memory address for a new array.
        # Without this, id(new_scene) == id(old_scene) is possible after the
        # old scene is freed, causing a cache hit on stale greyscale data.
        self._last_scene:      Optional[np.ndarray] = None
        self._last_scene_gray: Optional[np.ndarray] = None
        tpl_root = Path(template_dir)
        self._load_templates(tpl_root)
        self._load_roi_meta(tpl_root, templates_meta_file)

    def _normalize_template_name(self, template_name: str) -> str:
        """
        Normalize runtime template references to match loader keys.
        Supports both "btn_ok" and "btn_ok.png" style inputs.
        """
        return Path(template_name).stem.lower()

    @property
    def template_count(self) -> int:
        return len(self._templates)

    # ── Template Loading ──────────────────────────────────────────────────

    def _load_templates(self, directory: Path) -> None:
        """
        Recursively load all .png / .bmp files from *directory*.
        The template name is the filename without extension, in lowercase.
        """
        if not directory.exists():
            _log.warning(
                "Template directory '%s' not found — no templates loaded. "
                "Create the directory and add cropped UI screenshots.",
                directory,
            )
            return

        count = 0
        for img_path in sorted(directory.rglob("*.png")) + sorted(directory.rglob("*.bmp")):
            name  = img_path.stem.lower()
            image = cv2.imread(str(img_path), cv2.IMREAD_UNCHANGED)
            if image is None:
                _log.warning("Could not load template: %s", img_path)
                continue

            # Convert alpha-channel PNGs to BGR/Gray
            if image.ndim == 3 and image.shape[2] == 4:
                image = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)

            if self.use_grayscale:
                if image.ndim == 3:
                    image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

            self._templates[name] = image
            count += 1

        _log.info("Loaded %d template(s) from '%s'.", count, directory)

    def _load_roi_meta(self, template_dir: Path, meta_file: Optional[str]) -> None:
        """
        Optional JSON: { "btn_ok": { "roi": [x, y, w, h] }, ... }
        Keys are template stems; ROI limits matchTemplate search area (full frame if absent).
        """
        if not meta_file:
            return
        p = Path(meta_file)
        if not p.is_absolute():
            p = template_dir / p.name
        if not p.exists():
            _log.debug("templates_meta not found at %s (optional).", p)
            return
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:
            _log.warning("Could not parse templates_meta %s: %s", p, exc)
            return
        for key, val in data.items():
            if str(key).startswith("_") or not isinstance(val, dict):
                continue
            roi = val.get("roi")
            if isinstance(roi, (list, tuple)) and len(roi) == 4:
                stem = Path(str(key)).stem.lower()
                self._roi_by_template[stem] = (
                    int(roi[0]), int(roi[1]), int(roi[2]), int(roi[3]),
                )
        _log.info("Loaded %d ROI override(s) from '%s'.", len(self._roi_by_template), p)

    def reload_templates(self, template_dir: str = "templates") -> None:
        """Hot-reload all templates without restarting the bot."""
        self._templates.clear()
        self._roi_by_template.clear()
        self._missing_template_logged.clear()
        self._load_templates(Path(template_dir))

    # ── Core Matching ─────────────────────────────────────────────────────

    def _prepare_scene(
        self,
        scene: np.ndarray,
        roi: Optional[Tuple[int, int, int, int]] = None,
    ) -> Tuple[np.ndarray, int, int]:
        """
        Crop *scene* to *roi* (if given) and convert to grayscale if needed.

        Returns (prepared_image, roi_x_offset, roi_y_offset) so that match
        coordinates can be mapped back to the full frame.
        """
        # 1. Ensure we have the full grayscale scene cached if needed.
        #    Use Python identity (`is`) — NOT id() — so that two *different*
        #    NumPy arrays that happen to share the same memory address after
        #    the old one was freed are never confused for the same frame.
        if self.use_grayscale and scene.ndim == 3:
            if scene is not self._last_scene or self._last_scene_gray is None:
                self._last_scene      = scene   # hold ref → prevents address reuse
                self._last_scene_gray = cv2.cvtColor(scene, cv2.COLOR_BGR2GRAY)

            target_scene = self._last_scene_gray
        else:
            target_scene = scene

        # 2. Handle ROI (cropping)
        ox, oy = 0, 0
        if roi:
            rx, ry, rw, rh = roi
            # Crop the target (grayscale or color)
            target_scene = target_scene[ry:ry + rh, rx:rx + rw]
            ox, oy = rx, ry

        return target_scene, ox, oy

    def find(
        self,
        scene: np.ndarray,
        template_name: str,
        roi: Optional[Tuple[int, int, int, int]] = None,
        threshold: Optional[float] = None,
    ) -> Optional[MatchResult]:
        """
        Look for *template_name* in *scene*.

        Returns a MatchResult (name, confidence, (x, y, w, h)) in
        full-frame coordinates, or None if not found above threshold.
        """
        normalized_name = self._normalize_template_name(template_name)
        tpl = self._templates.get(normalized_name)
        if tpl is None:
            if normalized_name not in self._missing_template_logged:
                _log.warning(
                    "Template '%s' not loaded — skipping future misses for this name.",
                    normalized_name,
                )
                self._missing_template_logged.add(normalized_name)
            return None

        effective_roi = roi
        if effective_roi is None:
            effective_roi = self._roi_by_template.get(normalized_name)

        prepared, ox, oy = self._prepare_scene(scene, effective_roi)
        th = threshold if threshold is not None else self.threshold

        if prepared.shape[0] < tpl.shape[0] or prepared.shape[1] < tpl.shape[1]:
            _log.debug(
                "Scene (%dx%d) smaller than template (%dx%d) for '%s'.",
                prepared.shape[1], prepared.shape[0],
                tpl.shape[1],      tpl.shape[0],
                normalized_name,
            )
            return None

        # TM_CCOEFF_NORMED: +1 = perfect match, 0 = no correlation, -1 = inverted
        result  = cv2.matchTemplate(prepared, tpl, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)

        if max_val >= th:
            mx, my = max_loc
            th_h, th_w = tpl.shape[:2]
            bbox = (ox + mx, oy + my, th_w, th_h)
            _log.debug("FOUND '%s'  conf=%.3f  bbox=%s", normalized_name, max_val, bbox)
            return (normalized_name, max_val, bbox)

        _log.debug("  miss '%s'  conf=%.3f < %.3f", normalized_name, max_val, th)
        return None

    def find_any(
        self,
        scene: np.ndarray,
        template_names: List[str],
        roi: Optional[Tuple[int, int, int, int]] = None,
        threshold: Optional[float] = None,
    ) -> Optional[MatchResult]:
        """ Try each template in order. """
        for name in template_names:
            result = self.find(scene, name, roi=roi, threshold=threshold)
            if result:
                return result
        return None
