from __future__ import annotations

import logging
import os
import sys
from typing import Literal

import structlog
from structlog.types import Processor

LogFormat = Literal["json", "console"]


def configure(level: str | int = "INFO", fmt: LogFormat | None = None) -> None:
    if isinstance(level, str):
        level = level.upper()
    log_format: LogFormat = fmt or _detect_format()

    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    renderer: Processor
    if log_format == "json":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty())

    structlog.configure(
        processors=[*shared, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(_to_int(level)),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def _detect_format() -> LogFormat:
    raw = os.getenv("LOG_FORMAT", "").lower()
    if raw in {"json", "console"}:
        return raw  # type: ignore[return-value]
    return "console" if sys.stdout.isatty() else "json"


def _to_int(level: str | int) -> int:
    if isinstance(level, int):
        return level
    resolved = logging.getLevelName(level)
    if isinstance(resolved, int):
        return resolved
    return logging.INFO


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    bound = structlog.get_logger(name) if name else structlog.get_logger()
    return bound  # type: ignore[no-any-return]
