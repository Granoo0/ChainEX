"""
Lightweight tests for config path resolution and template naming.
Run: python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from config_loader import load_config  # noqa: E402
from paths import apply_path_resolution, PACKAGE_ROOT  # noqa: E402


class TestPathsAndConfig(unittest.TestCase):
    def test_apply_path_resolution_makes_log_absolute(self) -> None:
        cfg = load_config(str(ROOT / "config.json"))
        apply_path_resolution(cfg, PACKAGE_ROOT)
        log_file = cfg.get("logging", {}).get("log_file", "")
        self.assertTrue(Path(log_file).is_absolute())

    def test_template_stem_normalization(self) -> None:
        self.assertEqual(Path("1.png").stem.lower(), "1")
        self.assertEqual(Path("Foo.BMP").stem.lower(), "foo")


if __name__ == "__main__":
    unittest.main()
