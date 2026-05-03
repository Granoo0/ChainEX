"""
config_loader.py — Loads and validates config.json.

Provides sensible defaults so the bot can run even if the user has only
partially filled in the config file.
"""

import json
from pathlib import Path
from typing import Any


_DEFAULT_CONFIG: dict[str, Any] = {
    "window_title": "Ninja Saga",
    "vision": {
        "template_dir": "templates",
        "templates_meta_file": "templates/templates_meta.json",
        "match_threshold": 0.80,
        "use_grayscale": True,
    },
    "debug": {
        "enabled": False,
        "save_dir": "debug",
        "interval_ms": 500,
        "templates_to_draw": [],
        "opencv_window": False,
    },
    "window": {
        "pause_on_title_change": True,
    },
    "timing": {
        "cycle_debug_interval_ms": 2000,
        "max_runtime_minutes": 0,
        "reconnect_timeout_s": 0,
    },
    "input": {
        "focus_before_actions": False,
        "force_global_click": False,
    },
    "logging": {
        "level": "INFO",
        "log_file": "bot.log",
        "max_bytes": 5_242_880,
        "backup_count": 3,
    },
    "remote": {
        "enabled": True,
        "port": 8765,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into *base*, returning a new dict."""
    result = base.copy()
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def list_profiles(base_dir: str) -> list[str]:
    """Returns a list of .json files in the 'profiles' subdirectory."""
    p_dir = Path(base_dir) / "profiles"
    if not p_dir.exists():
        p_dir.mkdir(parents=True, exist_ok=True)
        return []
    return [f.name for f in p_dir.glob("*.json")]

def load_profile(name: str, base_dir: str) -> dict:
    """Loads a specific profile from the 'profiles' directory.
    Raises FileNotFoundError if the profile does not exist so callers
    can distinguish 'profile missing' from 'profile has bad JSON'.
    """
    path = Path(base_dir) / "profiles" / name
    if not path.exists():
        raise FileNotFoundError(
            f"Profile '{name}' not found in {Path(base_dir) / 'profiles'}. "
            "Check the filename or re-save the profile."
        )
    return load_config(str(path))


def load_config(path: str = "config.json") -> dict[str, Any]:
    """
    Read config.json from *path* (relative to CWD) and merge it with
    _DEFAULT_CONFIG so any missing keys use their defaults.
    """
    cfg_path = Path(path)

    if not cfg_path.exists():
        print(f"[ConfigLoader] '{path}' not found — using built-in defaults.")
        return _DEFAULT_CONFIG.copy()

    with cfg_path.open("r", encoding="utf-8") as fh:
        user_cfg = json.load(fh)

    # Strip comment keys that start with "_"
    user_cfg = {k: v for k, v in user_cfg.items() if not k.startswith("_")}

    merged = _deep_merge(_DEFAULT_CONFIG, user_cfg)
    return merged
