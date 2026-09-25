"""LangChain booking tool — thin adapter (SRP + DIP + OCP).

Business rules live in ``repository.StrictAppointmentValidator`` and
persistence in an ``AppointmentRepository``. This function only adapts
those to the LangChain ``@tool`` contract, so:

* new tools are added by registering them in ``TOOL_REGISTRY`` (OCP),
* the tool can be tested with a fake repository (DIP/LSP),
* validation/persistence changes don't touch this adapter's shape (SRP).
"""

from __future__ import annotations

from typing import Optional

from langchain_core.tools import tool

from modules.common.protocols import AppointmentRepository, AppointmentValidator
from modules.tools.repository import get_default_repository, get_default_validator


# OCP: register new clinic tools here; ToolExecutor iterates this list
# instead of hard-coding `if tool_name == ...` branches in the LLM service.
TOOL_REGISTRY: list = []


def _missing_message(missing: list[str]) -> str:
    return (
        "Cannot book yet — still missing: "
        + ", ".join(missing)
        + ". Ask the patient for the missing information first, "
        "then repeat all details and get confirmation before trying again."
    )


def make_booking_handler(
    repository: AppointmentRepository | None = None,
    validator: AppointmentValidator | None = None,
):
    """Factory (DIP): build the core booking use-case with injectable deps."""
    repo = repository or get_default_repository()
    val = validator or get_default_validator()

    async def handle(
        patient_name: Optional[str] = None,
        appointment_date: Optional[str] = None,
        appointment_time: Optional[str] = None,
    ) -> str:
        missing = val.missing_fields(patient_name, appointment_date, appointment_time)
        if missing:
            return _missing_message(missing)
        try:
            return await repo.book(patient_name or "", appointment_date or "", appointment_time or "")
        except Exception as error:
            print(f"[tool] booking failed: {error!r}")
            return (
                "Booking failed due to a technical issue. "
                "Apologize briefly and ask the patient to "
                "confirm the details so you can try again."
            )

    return handle


_default_handler = make_booking_handler()


@tool
async def book_appointment(
    patient_name: Optional[str] = None,
    appointment_date: Optional[str] = None,
    appointment_time: Optional[str] = None,
) -> str:
    """
    Book a dental appointment.

    Call this only after the patient has explicitly confirmed all three
    details: patient name, appointment date, and appointment time.
    If any detail is still unknown, do NOT call this tool — ask the
    patient for the missing information first.

    Args:
        patient_name: Full name of the patient.
        appointment_date: Requested appointment date (e.g. "2026-09-25").
        appointment_time: Requested appointment time (e.g. "10:30 AM").
    """

    # Args are intentionally optional in the schema: small models sometimes
    # emit partial/empty tool calls, and Groq rejects those server-side with
    # a 400 (tool_use_failed) when the fields are marked required. Handling
    # the missing info here keeps the conversation going instead of failing
    # the whole turn.
    return await _default_handler(
        patient_name=patient_name,
        appointment_date=appointment_date,
        appointment_time=appointment_time,
    )


TOOL_REGISTRY.append(book_appointment)
