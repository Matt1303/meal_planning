from __future__ import annotations

import uuid
from contextvars import ContextVar

import structlog

_CORRELATION_ID: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def new_correlation_id() -> str:
    return str(uuid.uuid4())


def set_correlation_id(value: str) -> None:
    _CORRELATION_ID.set(value)
    structlog.contextvars.bind_contextvars(correlation_id=value)


def current_correlation_id() -> str:
    value = _CORRELATION_ID.get()
    if value is None:
        value = new_correlation_id()
        set_correlation_id(value)
    return value


def reset() -> None:
    _CORRELATION_ID.set(None)
    structlog.contextvars.clear_contextvars()
