"""
paths.py — Single package root for resolving relative paths in config/logs/debug.

All paths in config that are not absolute are resolved against this directory.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

PACKAGE_ROOT: Path = Path(__file__).resolve().parent

# The config keys that hold filesystem paths (parent keys → leaf key).
# Used by both apply_path_resolution (relative→absolute) and
# make_paths_relative (absolute→relative) so the list is maintained once.
_PATH_FIELDS: tuple[tuple[str, ...], ...] = (
    ("logging", "log_file"),
    ("debug",   "save_dir"),
    ("vision",  "template_dir"),
    ("vision",  "templates_meta_file"),
)


def resolve_path(base: Path, value: str | Path) -> Path:
    """Resolve *value* relative to *base* if it is not already absolute."""
    p = Path(value)
    if p.is_absolute():
        return p
    return (base / p).resolve()


def apply_path_resolution(cfg: dict[str, Any], base: Path = PACKAGE_ROOT) -> dict[str, Any]:
    """
    Mutate *cfg* in-place: resolve all known path fields from relative → absolute.
    Safe to call multiple times (already-absolute paths are left unchanged).
    """
    for *parents, leaf in _PATH_FIELDS:
        node: Any = cfg
        for k in parents:
            node = node.setdefault(k, {}) if isinstance(node, dict) else {}
        if isinstance(node, dict) and node.get(leaf):
            node[leaf] = str(resolve_path(base, node[leaf]))
    return cfg


def make_paths_relative(cfg: dict[str, Any], base: Path = PACKAGE_ROOT) -> dict[str, Any]:
    """
    Return a *deep copy* of *cfg* with known absolute path fields converted back
    to paths relative to *base*.

    This is the inverse of apply_path_resolution and should be called just
    before writing config.json to disk so the saved file is portable — it will
    work on any machine regardless of where the application folder lives.

    Paths that are absolute but outside *base* (e.g. a custom log dir on a
    different drive) are left as-is so no information is silently lost.
    """
    out = copy.deepcopy(cfg)
    for *parents, leaf in _PATH_FIELDS:
        node: Any = out
        for k in parents:
            node = node.get(k, {}) if isinstance(node, dict) else {}
        if not isinstance(node, dict):
            continue
        val = node.get(leaf)
        if not val:
            continue
        p = Path(str(val))
        if p.is_absolute():
            try:
                node[leaf] = str(p.relative_to(base))
            except ValueError:
                pass  # outside base — keep absolute, user chose it explicitly
    return out
