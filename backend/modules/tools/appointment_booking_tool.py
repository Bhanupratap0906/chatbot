"""LangChain booking tool — thin adapter (SRP + DIP + OCP).

Business rules live in ``repository.RequiredAppointmentFieldsValidator`` and
persistence in an ``AppointmentRepository``. This function only adapts
those to the LangChain ``@tool`` contract, so:

* new tools are added by registering them in ``TOOL_REGISTRY`` (OCP),
* the tool can be tested with a fake repository (DIP/LSP),
* validation/persistence changes don't touch this adapter's shape (SRP).

Confirmation is NOT taken from the model: the turn manager sets
``BookingState.readback_done`` / ``user_confirmed`` (see booking_state.py).
"""

from __future__ import annotations

import logging
import re as _re
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from langchain_core.tools import tool

from modules.common.backend_contracts import AppointmentRepository, AppointmentValidator
from modules.common.booking_state import get_booking_state, normalize_time
from modules.common.settings import get_settings
from modules.tools.fallback_appointment_repository import get_default_repository, get_default_validator
from modules.tools.tool_registry import TOOL_REGISTRY

logger = logging.getLogger("tool")

# Re-export: existing importers read TOOL_REGISTRY from this module.
__all__ = ["TOOL_REGISTRY", "book_appointment", "create_appointment_booking_handler"]


def _format_missing_booking_fields_message(missing: list[str]) -> str:
    """Format a prompt for absent booking details; used by the booking handler."""
    return (
        "Cannot book yet — still missing: "
        + ", ".join(missing)
        + ". Ask the patient for ONE missing item now. Do not read back or "
        "confirm until nothing is missing."
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
        # 1. Every required field must be present (DOB and gender included).
        missing = list(
            val.missing_fields(patient_name, appointment_date, appointment_time, mobile_number)
        )
        if not (birth_date or "").strip():
            missing.append("date of birth")
        if not (gender or "").strip():
            missing.append("gender")
        if missing:
            return _format_missing_booking_fields_message(missing)

        # 2. Service/problem is needed to map to a clinic service.
        if not str(service or "").strip():
            return (
                "Booking not submitted: ask what problem or symptom the patient "
                "needs help with, then match it to a clinic service."
            )

        # 3. Real confirmation, owned by the turn manager (not by the model).
        state = get_booking_state()
        if not state.readback_done:
            return (
                "Booking not submitted: read all details back to the caller in one "
                "sentence and ask 'Is that correct?' first."
            )
        if not state.user_confirmed:
            return "Booking not submitted: wait for the caller to say yes to the read-back."
        if confirmed_by_patient is not True:
            return "Booking not submitted: explicit patient confirmation is required."

        # 4. Mobile number.
        mobile_digits = _re.sub(r"\D", "", mobile_number or "")
        if not 10 <= len(mobile_digits) <= 15:
            return "I need the caller's real mobile number. Ask for it."

        # 5. Birth date.
        try:
            bd = datetime.strptime((birth_date or "").strip(), "%Y-%m-%d")
        except ValueError:
            return "That date of birth doesn't look right. Ask for it again."
        clinic_today = datetime.now(ZoneInfo(get_settings().clinic_timezone)).date()
        if bd.date() > clinic_today:
            return "Date of birth can't be in the future. Ask again."

        # 6. Appointment date.
        parsed_appt = None
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d-%m-%Y", "%B %d %Y", "%b %d %Y"):
            try:
                parsed_appt = datetime.strptime((appointment_date or "").strip(), fmt)
                break
            except ValueError:
                continue
        if parsed_appt is None:
            return "That appointment date doesn't look right. Ask for it again."
        if parsed_appt.date() < clinic_today:
            return "The appointment date must be today or later. Ask for a new date."

        # 7. Appointment time must be one of the slots actually offered.
        normalized_time = normalize_time(appointment_time)
        if normalized_time is None:
            return "That appointment time doesn't look right. Ask the caller to pick one of the offered times."
        if not state.offered_slots:
            return "Booking not submitted: check availability and offer appointment times first."
        if normalized_time not in state.offered_slots:
            return "That time wasn't offered. Ask the caller to choose one of the available times."
        if state.selected_slot != normalized_time:
            return "Booking not submitted: ask the caller to choose one of the offered times before booking."

        # 8. Persist.
        try:
            result = await repo.book(
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

        # Require a fresh read-back + yes for any further booking.
        state.reset_confirmation()
        state.offered_slots.clear()
        state.selected_slot = None
        return result

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

    Call only after the caller said yes to the read-back of all details and
    every argument is known. Use the exact slot the caller chose.

    Args:
        patient_name: Full name of the patient.
        birth_date: Date of birth, YYYY-MM-DD.
        mobile_number: Phone number as the caller gave it (10-15 digits).
        gender: Gender as stated by the caller.
        appointment_date: Requested date, e.g. "2026-10-30".
        appointment_time: The chosen offered slot, e.g. "10:30 AM".
        provider: Optional; only for a caller-requested provider preference.
        service: The patient's problem in their own words (backend maps it).
        confirmed_by_patient: true only after the caller's clear yes.
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
