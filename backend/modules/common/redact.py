"""PII redaction helper (SRP).

``redact()`` masks patient-identifying values (names, mobile numbers, dates
of birth, gender) before they are written to logs. It is intentionally
lossy: callers keep the original value in memory and only the redacted form
reaches log lines.
"""

from __future__ import annotations

from typing import Any


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

