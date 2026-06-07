"""Logging configuration: rotating file handler + console handler."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import config

_CONFIGURED = False


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """Configure the ``notify_monitor`` logger once and return it."""
    global _CONFIGURED
    logger = logging.getLogger("notify_monitor")
    if _CONFIGURED:
        return logger

    logger.setLevel(level)
    logger.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)

    log_path = Path(config.LOG_PATH)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        log_path, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    _CONFIGURED = True
    logger.info("Logging initialized -> %s", log_path)
    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a child logger under the ``notify_monitor`` namespace."""
    base = setup_logging()
    return base.getChild(name) if name else base
