"""Shared date/time parsing used by workflow, booking, and tools."""

from __future__ import annotations

import re
from datetime import date, datetime


def normalize_date_str(date_str: str) -> date | None:
    text = (date_str or "").strip()
    text = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", text, flags=re.IGNORECASE)
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d-%m-%Y", "%B %d %Y", "%b %d %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def normalize_time_str(time_str: str) -> str | None:
    text = (time_str or "").strip().upper()
    # Handle "noon", "midnight"
    if text == "NOON":
        return "12:00"
    if text == "MIDNIGHT":
        return "00:00"
    for fmt in ("%I:%M %p", "%I:%M%p", "%H:%M", "%I %p", "%I%p"):
        try:
            return datetime.strptime(text, fmt).strftime("%H:%M")
        except ValueError:
            continue
    # Try digit-only like "11am", "11:30am"
    m = re.match(r"(\d{1,2})(?::(\d{2}))?\s*([AP]M)?", text)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or "0")
        ampm = (m.group(3) or "AM").upper()
        if ampm == "PM" and hour < 12:
            hour += 12
        elif ampm == "AM" and hour == 12:
            hour = 0
        return f"{hour:02d}:{minute:02d}"
    return None


def normalize_phone_str(phone_str: str) -> str:
    return re.sub(r"\D", "", str(phone_str or ""))
