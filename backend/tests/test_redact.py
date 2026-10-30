"""Tests for the PII redact() helper."""

from __future__ import annotations

from modules.common.redact import redact


def test_none_and_empty() -> None:
    assert redact(None) == ""
    assert redact("") == ""


def test_phone_keeps_last_four() -> None:
    assert redact("9876543210") == "******3210"


def test_date_keeps_year() -> None:
    assert redact("1990-05-21") == "1990-**-**"


def test_multi_word_name_keeps_first_letters() -> None:
    assert redact("John Doe") == "J*** D***"


def test_single_value_keeps_first_char() -> None:
    assert redact("Alice") == "A***"


def test_gender() -> None:
    assert redact("female") == "f***"


def test_log_redaction_toggle(monkeypatch) -> None:
    monkeypatch.setenv("LOG_REDACTION", "false")
    assert redact("John Doe") == "John Doe"
    assert redact("9876543210") == "9876543210"

    monkeypatch.setenv("LOG_REDACTION", "true")
    assert redact("John Doe") == "J*** D***"
