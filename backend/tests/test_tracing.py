"""Item 22: LangSmith tracing anonymization."""

from __future__ import annotations

import os

from langsmith.anonymizer import create_anonymizer

from modules.common.redact import redact
from modules.common.tracing import init_tracing


def test_anonymizer_redacts_pii() -> None:
    anonymizer = create_anonymizer(redact)
    payload = {
        "phone": "9876543210",
        "name": "John Doe",
        "dob": "1990-05-21",
        "nested": {"note": "patient John Doe phone 9876543210"},
    }
    out = anonymizer(payload)
    serialized = str(out)
    assert "9876543210" not in serialized
    assert "John Doe" not in serialized
    assert "1990-05-21" not in serialized


def test_init_tracing_noop_without_env(monkeypatch) -> None:
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    # Should not raise
    init_tracing()
