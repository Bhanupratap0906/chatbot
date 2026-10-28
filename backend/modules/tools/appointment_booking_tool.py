"""LangChain booking tool — thin adapter (SRP + DIP + OCP).

Business rules live in ``repository.RequiredAppointmentFieldsValidator`` and
persistence in an ``AppointmentRepository``. This function only adapts
those to the LangChain ``@tool`` contract, so:

* new tools are added by registering them in ``TOOL_REGISTRY`` (OCP),
* the tool can be tested with a fake repository (DIP/LSP),
* validation/persistence changes don't touch this adapter's shape (SRP).
"""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional

from langchain_core.tools import tool

from modules.common.backend_contracts import AppointmentRepository, AppointmentValidator
from modules.common.settings import get_settings
from modules.tools.tool_registry import TOOL_REGISTRY
from modules.tools.fallback_appointment_repository import get_default_repository, get_default_validator

logger = logging.getLogger("tool")

# Re-export: existing importers read TOOL_REGISTRY from this module.
__all__ = ["TOOL_REGISTRY", "book_appointment", "create_appointment_booking_handler"]


def _format_missing_booking_fields_message(missing: list[str]) -> str:
    """Format a prompt for absent booking details; used by the booking handler."""
    return (
        "Cannot book yet — still missing: "
        + ", ".join(missing)
        + ". Ask the patient for the missing information first, "
        "then repeat all details and get confirmation before trying again."
    )


def create_appointment_booking_handler(
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
        mobile_number: Optional[str] = None,
        provider: Optional[str] = None,
        service: Optional[str] = None,
        confirmed_by_patient: Optional[bool] = None,
        birth_date: Optional[str] = None,
        gender: Optional[str] = None,
    ) -> str:
        """Validate booking details then persist them; called by the LangChain booking tool."""
        missing = val.missing_fields(
            patient_name, appointment_date, appointment_time, mobile_number
        )
        if missing:
            return _format_missing_booking_fields_message(missing)
        if confirmed_by_patient is not True:
            return "Booking not submitted: explicit patient confirmation is required."
        if not str(service or "").strip():
            return "Booking not submitted: ask what problem or symptom the patient needs help with, then match it to a clinic service."

        # Validate mobile number.
        import re as _re

        mobile_digits = _re.sub(r"\D", "", mobile_number or "")
        if not 10 <= len(mobile_digits) <= 15:
            return "I need the caller's real mobile number. Ask for it."

        # Validate birth_date if provided.
        if birth_date:
            try:
                bd = datetime.strptime(birth_date.strip(), "%Y-%m-%d")
            except ValueError:
                return "That date of birth doesn't look right. Ask for it as YYYY-MM-DD."
            if bd.date() > datetime.now(ZoneInfo(get_settings().clinic_timezone)).date():
                return "Date of birth can't be in the future. Ask again."

        # Validate appointment_date.
        if appointment_date:
            parsed_appt = None
            for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d-%m-%Y", "%B %d %Y", "%b %d %Y"):
                try:
                    parsed_appt = datetime.strptime(appointment_date.strip(), fmt)
                    break
                except ValueError:
                    continue
            if parsed_appt is None:
                return "That appointment date doesn't look right. Ask for it again."
            if parsed_appt.date() < datetime.now(ZoneInfo(get_settings().clinic_timezone)).date():
                return "The appointment date must be today or later. Ask for a new date."

        try:
            return await repo.book(
                patient_name or "",
                appointment_date or "",
                appointment_time or "",
                mobile_number or "",
                provider=provider,
                service=service,
                birth_date=birth_date,
                gender=gender,
            )
        except Exception as error:
            logger.warning("booking failed: %r", error)
            return (
                "Booking failed due to a technical issue. "
                "Apologize briefly and ask the patient to "
                "confirm the details so you can try again."
            )

    return handle


def _create_default_portal_booking_handler():
    """Use the real portal repository by default (DIP: injectable per call site)."""
    from modules.tools.portal_appointment_booking_repository import PortalAppointmentBookingRepository

    return create_appointment_booking_handler(repository=PortalAppointmentBookingRepository())


_default_appointment_booking_handler = _create_default_portal_booking_handler()


@tool
async def book_appointment(
    patient_name: Optional[str] = None,
    appointment_date: Optional[str] = None,
    appointment_time: Optional[str] = None,
    mobile_number: Optional[str] = None,
    provider: Optional[str] = None,
    service: Optional[str] = None,
    confirmed_by_patient: Optional[bool] = None,
    birth_date: Optional[str] = None,
    gender: Optional[str] = None,
) -> str:
    """
    Book a dental appointment.

    Call this only after the patient has explicitly confirmed: patient name,
    appointment date, appointment time, and the mobile number on their
    record. If any of those is still unknown, do NOT call this tool — ask
    the patient for the missing information first.

    Args:
        patient_name: Full name of the patient.
        appointment_date: Requested appointment date (e.g. "2026-09-25").
        appointment_time: Requested appointment time (e.g. "10:30 AM").
        mobile_number: Phone number on the patient's record (10-15 digits),
            used to find the patient in the clinic system.
        provider: Optional provider preference if explicitly requested;
            otherwise the clinic's first configured provider is selected.
        service: The patient's problem or symptom in their own words. The
            backend maps this description to a configured clinic service.
        confirmed_by_patient: Must be true only after the patient confirms all booking details.
    """

    # Args are intentionally optional in the schema: small models sometimes
    # emit partial/empty tool calls, and Groq rejects those server-side with
    # a 400 (tool_use_failed) when the fields are marked required. Handling
    # the missing info here keeps the conversation going instead of failing
    # the whole turn.
    return await _default_appointment_booking_handler(
        patient_name=patient_name,
        appointment_date=appointment_date,
        appointment_time=appointment_time,
        mobile_number=mobile_number,
        provider=provider,
        service=service,
        confirmed_by_patient=confirmed_by_patient,
        birth_date=birth_date,
        gender=gender,
    )


TOOL_REGISTRY.append(book_appointment)
