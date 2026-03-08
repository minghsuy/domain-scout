"""Shared logging configuration for domain-scout."""

from __future__ import annotations

import logging
import sys

import structlog


def configure_logging(
    level: int = logging.WARNING,
    stderr: bool = True,
) -> None:
    """Configure structlog for domain-scout.

    Called automatically at import time with library-safe defaults
    (WARNING level, output to stderr).  The CLI overrides to INFO/DEBUG.

    Library consumers can call this again to change the level::

        from domain_scout import configure_logging
        configure_logging(level=logging.DEBUG)
    """
    factory: structlog.PrintLoggerFactory | None = None
    if stderr:
        factory = structlog.PrintLoggerFactory(file=sys.stderr)

    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(level),
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        logger_factory=factory,
    )
