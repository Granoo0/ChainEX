"""
tests/test_config.py — Unit tests for config_loader and paths.

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

from config_loader import load_config, _deep_merge  # noqa: E402
from paths import apply_path_resolution, make_paths_relative, PACKAGE_ROOT  # noqa: E402


# ── _deep_merge ────────────────────────────────────────────────────────────────

class TestDeepMerge(unittest.TestCase):

    def test_simple_override_replaces_value(self):
        result = _deep_merge({"a": 1}, {"a": 99})
        self.assertEqual(result["a"], 99)

    def test_unrelated_base_keys_preserved(self):
        result = _deep_merge({"a": 1, "b": 2}, {"a": 99})
        self.assertEqual(result["b"], 2)

    def test_new_key_in_override_added(self):
        result = _deep_merge({"a": 1}, {"z": 42})
        self.assertEqual(result["z"], 42)

    def test_nested_dicts_merged_recursively(self):
        base     = {"vision": {"threshold": 0.8, "grayscale": True}}
        override = {"vision": {"threshold": 0.9}}
        result   = _deep_merge(base, override)
        self.assertEqual(result["vision"]["threshold"], 0.9)
        self.assertTrue(result["vision"]["grayscale"])  # preserved

    def test_nested_dict_overridden_by_scalar(self):
        """When override provides a scalar where base had a dict, scalar wins."""
        base     = {"vision": {"threshold": 0.8}}
        override = {"vision": "disabled"}
        result   = _deep_merge(base, override)
        self.assertEqual(result["vision"], "disabled")

    def test_original_not_mutated(self):
        base     = {"a": {"x": 1}}
        override = {"a": {"x": 99}}
        _deep_merge(base, override)
        self.assertEqual(base["a"]["x"], 1)  # base must be unchanged


# ── load_config ────────────────────────────────────────────────────────────────

class TestLoadConfig(unittest.TestCase):

    def _defaults(self) -> dict:
        """Load with a path that cannot exist so we always get built-in defaults."""
        return load_config("/this/path/does/not/exist.json")

    def test_missing_file_returns_defaults_without_crash(self):
        cfg = self._defaults()
        self.assertIsInstance(cfg, dict)

    # ── Required top-level sections ──────────────────────────────────────────

    def test_window_title_default_present(self):
        cfg = self._defaults()
        self.assertIn("window_title", cfg)

    def test_vision_section_present(self):
        cfg = self._defaults()
        self.assertIn("vision", cfg)
        self.assertIn("template_dir", cfg["vision"])
        self.assertIn("match_threshold", cfg["vision"])

    def test_logging_section_present(self):
        cfg = self._defaults()
        self.assertIn("logging", cfg)
        self.assertIn("level", cfg["logging"])
        self.assertIn("log_file", cfg["logging"])

    def test_remote_section_present(self):
        cfg = self._defaults()
        self.assertIn("remote", cfg)
        self.assertIn("port", cfg["remote"])

    def test_timing_reconnect_has_default(self):
        """reconnect_timeout_s must be in defaults so _attempt_reconnect degrades gracefully."""
        cfg = self._defaults()
        self.assertIn("reconnect_timeout_s", cfg.get("timing", {}))
        self.assertEqual(cfg["timing"]["reconnect_timeout_s"], 0)

    # ── Dead sections must be gone ───────────────────────────────────────────

    def test_dead_missions_section_absent(self):
        self.assertNotIn("missions", self._defaults())

    def test_dead_exams_section_absent(self):
        self.assertNotIn("exams", self._defaults())

    def test_dead_combat_section_absent(self):
        self.assertNotIn("combat", self._defaults())

    def test_dead_state_template_audit_absent(self):
        self.assertNotIn("state_template_audit", self._defaults())

    def test_dead_recovery_section_absent(self):
        self.assertNotIn("recovery", self._defaults())

    # ── Partial override merges correctly ────────────────────────────────────

    def test_partial_override_merged(self):
        """Only the keys provided by the user override defaults; others survive."""
        import tempfile, json, os
        user = {"vision": {"match_threshold": 0.95}}
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump(user, f)
            tmp = f.name
        try:
            cfg = load_config(tmp)
            self.assertAlmostEqual(cfg["vision"]["match_threshold"], 0.95)
            # Default keys in vision should still be present
            self.assertIn("template_dir", cfg["vision"])
        finally:
            os.unlink(tmp)

    def test_comment_keys_stripped(self):
        """Keys starting with '_' in config.json must be ignored."""
        import tempfile, json, os
        user = {"_comment": "ignored", "window_title": "MyGame"}
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump(user, f)
            tmp = f.name
        try:
            cfg = load_config(tmp)
            self.assertNotIn("_comment", cfg)
            self.assertEqual(cfg["window_title"], "MyGame")
        finally:
            os.unlink(tmp)


# ── paths ──────────────────────────────────────────────────────────────────────

class TestPaths(unittest.TestCase):

    def test_log_file_made_absolute(self):
        cfg = load_config("/nonexistent.json")
        apply_path_resolution(cfg, PACKAGE_ROOT)
        log = cfg.get("logging", {}).get("log_file", "")
        self.assertTrue(Path(log).is_absolute(), f"log_file not absolute: {log!r}")

    def test_template_dir_made_absolute(self):
        cfg = load_config("/nonexistent.json")
        apply_path_resolution(cfg, PACKAGE_ROOT)
        td = cfg.get("vision", {}).get("template_dir", "")
        self.assertTrue(Path(td).is_absolute(), f"template_dir not absolute: {td!r}")

    def test_make_paths_relative_inverts_resolution(self):
        cfg = load_config("/nonexistent.json")
        apply_path_resolution(cfg, PACKAGE_ROOT)
        rel = make_paths_relative(cfg, PACKAGE_ROOT)
        log = rel.get("logging", {}).get("log_file", "")
        self.assertFalse(Path(log).is_absolute(), f"log_file should be relative: {log!r}")

    def test_apply_path_resolution_idempotent(self):
        cfg = load_config("/nonexistent.json")
        apply_path_resolution(cfg, PACKAGE_ROOT)
        val1 = cfg.get("logging", {}).get("log_file")
        apply_path_resolution(cfg, PACKAGE_ROOT)
        val2 = cfg.get("logging", {}).get("log_file")
        self.assertEqual(val1, val2)


if __name__ == "__main__":
    unittest.main()
