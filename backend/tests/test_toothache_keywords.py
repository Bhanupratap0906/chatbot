"""Item 24: toothache keyword mapping."""

from __future__ import annotations

from modules.tools.portal_appointment_booking_repository import (
    _map_patient_problem_to_service_keywords,
)


def test_toothache_keywords_include_filling() -> None:
    kw = _map_patient_problem_to_service_keywords("I have a toothache")
    assert "toothache" in kw
    assert "filling" in kw
