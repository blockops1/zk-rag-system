#!/usr/bin/env python3
"""
_log.py — Shared logging utility for RAG pipeline scripts.

Usage:
    from _log import get_logger
    log = get_logger(__name__, log_group="main")
    log.info("something happened")
    log("something with level", level=logging.ERROR)   # backward-compat style

Output goes to two consolidated logs:
  - pipeline_main.log    (log_group="main")
  - pipeline_harvest.log (log_group="harvest")

Format: [YYYY-MM-DD HH:MM:SS] [SCRIPT_NAME] message
Both file and stdout are written.
"""

import logging
import sys
from pathlib import Path

LOG_DIR = Path("rag/logs")
LOG_FILES = {
    "main":    LOG_DIR / "pipeline_main.log",
    "harvest": LOG_DIR / "pipeline_harvest.log",
}


class _LogWrapper:
    """Wraps a logger to also support callable syntax: log(msg, level=...).

    This preserves backward compatibility with scripts that call:
        log("message", level=logging.ERROR)
    """

    def __init__(self, logger: logging.Logger):
        object.__setattr__(self, "_logger", logger)

    def __call__(self, msg: str, level: int = logging.INFO):
        self._logger.log(level, msg)

    def __getattr__(self, name: str):
        return getattr(self._logger, name)


# Module-level registry so all loggers share the same file handlers
_registered: set = set()


def get_logger(
    name: str,
    log_group: str = "main",
    level: int = logging.INFO,
) -> _LogWrapper:
    """Return a configured logger that writes to consolidated log + stdout.

    Args:
        name: Logger name (use __name__ for module-level loggers).
              Will be used as the script prefix in the log line.
        log_group: "main" for core pipeline, "harvest" for harvesting scripts.
        level: Logging level (default INFO).

    Returns:
        A _LogWrapper that supports both logger methods (log.info, log.error, etc.)
        and callable syntax (log("msg", level=...)).
    """
    if log_group not in LOG_FILES:
        raise ValueError(f"log_group must be one of {list(LOG_FILES.keys())}, got {log_group!r}")

    logger = logging.getLogger(name)
    if name in _registered:
        return _LogWrapper(logger)
    _registered.add(name)

    logger.setLevel(level)
    logger.propagate = False

    # Console handler — stdout, no prefix (cleaner for terminal)
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(console)

    # File handler — consolidated log for this group
    log_file = LOG_FILES[log_group]
    log_file.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(level)

    # Format: [YYYY-MM-DD HH:MM:SS] [SCRIPT_NAME] message
    # SCRIPT_NAME is left-aligned, 12 chars
    script_name = name.upper()[:12].ljust(12)
    file_handler.setFormatter(logging.Formatter(
        f"[%(asctime)s] [{script_name}] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(file_handler)

    return _LogWrapper(logger)
