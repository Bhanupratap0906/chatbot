"""PII redaction helper (SRP).

``redact()`` masks patient-identifying values (names, mobile numbers, dates
of birth, gender) before they are written to logs. It is intentionally
lossy: callers keep the original value in memory and only the redacted form
reaches log lines.

Set ``LOG_REDACTION=false`` in the environment to log original text verbatim
(temp development/debug use only — transcripts contain patient data).
"""

from __future__ import annotations

import os

from typing import Any


def _redaction_enabled() -> bool:
    """Read ``LOG_REDACTION`` at call time so ``.env`` values (loaded by the
    settings module, possibly after this module was imported) are honored."""
    return os.getenv("LOG_REDACTION", "true").strip().lower() in (
        "1", "true", "yes", "on",
    )


def redact(value: Any) -> str:
    """Return a redacted representation of a potentially sensitive value.

    * Phone numbers (mostly digits) keep only the last four digits.
    * Dates (``YYYY-MM-DD`` / ``YYYY/MM/DD``) keep only the year.
    * Multi-word names keep the first letter of each word.
    * Other values keep only the first character.
    """
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return text

    if not _redaction_enabled():
        return text

    digits = "".join(ch for ch in text if ch.isdigit())
    # Date check first (a date also contains many digits).
    if len(text) == 10 and text[4] in "-/" and text[7] in "-/":
        return text[:4] + text[4] + "**" + text[7] + "**"

    if len(digits) >= 7 and len(digits) / max(len(text), 1) >= 0.5:
        if len(text) <= 4:
            return "*" * len(text)
        return "*" * (len(text) - 4) + text[-4:]

    parts = text.split()
    if len(parts) > 1:
        return " ".join((part[0] + "***") if part else "" for part in parts)
    if len(text) <= 1:
        return "*"
    return text[0] + "***"


__all__ = ["redact"]
