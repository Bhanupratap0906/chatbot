"""Per-call context (call_id) and JSON logging helpers (SRP)."""

from __future__ import annotations

import contextvars
import json
import logging
import uuid
from typing import Any


_call_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("call_id", default="")


def get_call_id() -> str:
    """Return the current call id (empty string if unset)."""
    return _call_id_var.get()


def set_call_id(value: str | None = None) -> str:
    """Set the call id for the current context; generate one if not provided."""
    cid = value or str(uuid.uuid4())
    _call_id_var.set(cid)
    return cid


def new_call_id() -> str:
    return set_call_id()


class CallIDFilter(logging.Filter):
    """Inject ``call_id`` into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.call_id = get_call_id() or "-"
        return True


class JSONFormatter(logging.Formatter):
    """Render a log record as a single JSON object (structured logging)."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "time": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "call_id": getattr(record, "call_id", "-"),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload)


__all__ = ["get_call_id", "set_call_id", "new_call_id", "CallIDFilter", "JSONFormatter"]
