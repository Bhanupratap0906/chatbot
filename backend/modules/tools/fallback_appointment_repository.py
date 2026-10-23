"""Appointment persistence + validation (SRP).

* ``AppointmentRepository``: persistence only.
* ``RequiredAppointmentFieldsValidator``: field-presence rules only.
* The LangChain ``@tool`` in ``appointment.py`` becomes a thin adapter
  over these two abstractions (DIP), so booking logic can be unit-tested
  and swapped without touching the LLM.
"""

from __future__ import annotations

import logging
from typing import Optional

from modules.common.backend_contracts import AppointmentRepository as IAppointmentRepository
from modules.common.backend_contracts import AppointmentValidator as IAppointmentValidator

logger = logging.getLogger("appointment")


REQUIRED_FIELDS = ("patient_name", "appointment_date", "appointment_time", "mobile_number")


class FallbackLoggingAppointmentRepository(IAppointmentRepository):
    """Fallback stub: logs + returns a confirmation string (no portal token).

    OCP: ``PortalAppointmentBookingRepository`` (``modules/tools/portal_repository``)
    is the real API implementation and is registered as the default; this
    class stays as the dependency-free fallback for tests/offline runs.
    LSP: any subclass/instance can be substituted wherever the interface
    is expected.
    """

    async def book(
        self,
        patient_name: str,
        appointment_date: str,
        appointment_time: str,
        mobile_number: str,
        *,
        provider: str | None = None,
        service: str | None = None,
        birth_date: str | None = None,
        gender: str | None = None,
    ) -> str:
        """Log and confirm a booking; called by the appointment handler after validation."""
        name = patient_name.strip()
        date = appointment_date.strip()
        time = appointment_time.strip()
        phone = mobile_number.strip()
        logger.info(
            "fallback booking patient=%s phone=%s date=%s time=%s provider=%s service=%s",
            name, phone, date, time, provider or "-", service or "-",
        )
        return f"Appointment successfully booked for {name} on {date} at {time}."


class RequiredAppointmentFieldsValidator(IAppointmentValidator):
    """SRP: only decides which required fields are missing."""

    def __init__(self, required: tuple[str, ...] = REQUIRED_FIELDS):
        """Set required booking fields; used when constructing the booking handler."""
        self._required = required

    def missing_fields(
        self,
        patient_name: Optional[str],
        appointment_date: Optional[str],
        appointment_time: Optional[str],
        mobile_number: Optional[str],
    ) -> list[str]:
        """Return empty or missing field names; called before the repository books an appointment."""
        values = {
            "patient_name": patient_name,
            "appointment_date": appointment_date,
            "appointment_time": appointment_time,
            "mobile_number": mobile_number,
        }
        return [
            field
            for field in self._required
            if not (values.get(field) or "").strip()
        ]


_DEFAULT_REPOSITORY = FallbackLoggingAppointmentRepository()
_DEFAULT_VALIDATOR = RequiredAppointmentFieldsValidator()


def get_default_repository() -> IAppointmentRepository:
    """Return the shared booking repository; used by the default appointment handler."""
    return _DEFAULT_REPOSITORY


def get_default_validator() -> IAppointmentValidator:
    """Return the shared field validator; used by the default appointment handler."""
    return _DEFAULT_VALIDATOR
