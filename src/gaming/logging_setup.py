"""Logging configuration for the gaming tool."""

from __future__ import annotations

import logging
import sys

_CONFIGURED = False


def setup_logging(level: str = "INFO", *, quiet: bool = False) -> logging.Logger:
    """Configure the root ``gaming`` logger.

    Idempotent: repeated calls only adjust the level.
    """
    global _CONFIGURED
    logger = logging.getLogger("gaming")

    numeric = getattr(logging, str(level).upper(), logging.INFO)
    if quiet:
        numeric = logging.ERROR
    logger.setLevel(numeric)

    if not _CONFIGURED:
        handler = logging.StreamHandler(stream=sys.stderr)
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        logger.addHandler(handler)
        logger.propagate = False
        _CONFIGURED = True

    return logger


def get_logger(name: str = "gaming") -> logging.Logger:
    return logging.getLogger(name)
