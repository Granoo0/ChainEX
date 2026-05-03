"""
bot_logger.py — Centralised logging setup.

Creates a rotating-file handler + a coloured console handler so you can
watch the bot in your terminal while also keeping a persistent log on disk.

Usage:
    from bot_logger import get_logger
    log = get_logger("MyModule")
    log.info("Hello %s", "world")
"""

import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Optional


# ── ANSI colour codes for the console handler ──────────────────────────────
_COLOURS = {
    "DEBUG":    "\033[36m",   # Cyan
    "INFO":     "\033[32m",   # Green
    "WARNING":  "\033[33m",   # Yellow
    "ERROR":    "\033[31m",   # Red
    "CRITICAL": "\033[35m",   # Magenta
    "RESET":    "\033[0m",
}


class _ColouredFormatter(logging.Formatter):
    """Inject ANSI colour codes around the level-name in console output."""

    _FMT = "%(asctime)s  %(levelname)-8s  [%(name)s]  %(message)s"

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        colour = _COLOURS.get(record.levelname, "")
        reset  = _COLOURS["RESET"]
        # Save and restore levelname so we don't mutate the shared LogRecord.
        # Without this the file handler (which runs after us) would write
        # ANSI escape codes into bot.log instead of the plain level name.
        orig_levelname    = record.levelname
        record.levelname  = f"{colour}{orig_levelname}{reset}"
        result            = logging.Formatter(self._FMT, datefmt="%H:%M:%S").format(record)
        record.levelname  = orig_levelname   # restore for subsequent handlers
        return result


# Module-level singleton so every call to get_logger() shares the same
# root-level configuration.
_root_configured = False


def _configure_root(
    level: str    = "DEBUG",
    log_file: str = "bot.log",
    max_bytes: int = 5_242_880,   # 5 MB
    backup_count: int = 3,
    *,
    force: bool = False,
) -> None:
    global _root_configured
    if _root_configured and not force:
        return
    _root_configured = True

    root = logging.getLogger("G Panel")
    root.setLevel(getattr(logging, level.upper(), logging.DEBUG))

    if force:
        for h in list(root.handlers):
            root.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass

    # ── Console handler ──────────────────────────────────────────────────
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.DEBUG)
    ch.setFormatter(_ColouredFormatter())
    root.addHandler(ch)

    # ── Rotating file handler ────────────────────────────────────────────
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fh = logging.handlers.RotatingFileHandler(
        filename     = Path(log_file),
        maxBytes     = max_bytes,
        backupCount  = backup_count,
        encoding     = "utf-8",
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(
        logging.Formatter(
            "%(asctime)s  %(levelname)-8s  [%(name)s]  %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root.addHandler(fh)


def configure_logging_from_config(cfg: dict) -> None:
    """
    Apply logging section from config (after path resolution). Replaces handlers.
    Call once at startup from main/launcher after load_config + apply_path_resolution.
    """
    _configure_root(
        level        = cfg.get("level", "DEBUG"),
        log_file     = cfg.get("log_file", "bot.log"),
        max_bytes    = cfg.get("max_bytes", 5_242_880),
        backup_count = cfg.get("backup_count", 3),
        force        = True,
    )


def get_logger(name: str = "Bot", cfg: Optional[dict] = None) -> logging.Logger:
    """
    Return a child logger under the 'G Panel' root.

    If *cfg* is provided (the logging sub-dict from config.json), the root
    logger is (re-)configured on first call.
    Prefer configure_logging_from_config() at startup for resolved paths.
    """
    if cfg:
        _configure_root(
            level        = cfg.get("level", "DEBUG"),
            log_file     = cfg.get("log_file", "bot.log"),
            max_bytes    = cfg.get("max_bytes", 5_242_880),
            backup_count = cfg.get("backup_count", 3),
        )
    else:
        _configure_root()   # Use built-in defaults

    return logging.getLogger(f"G Panel.{name}")
