"""Appointment persistence + validation (SRP).

* ``AppointmentRepository``: persistence only.
* ``StrictAppointmentValidator``: field-presence rules only.
* The LangChain ``@tool`` in ``appointment.py`` becomes a thin adapter
  over these two abstractions (DIP), so booking logic can be unit-tested
  and swapped without touching the LLM.
"""

from __future__ import annotations

from typing import Optional

from modules.common.protocols import AppointmentRepository as IAppointmentRepository
from modules.common.protocols import AppointmentValidator as IAppointmentValidator


REQUIRED_FIELDS = ("patient_name", "appointment_date", "appointment_time")


class LoggingAppointmentRepository(IAppointmentRepository):
    """Current stub: logs + returns a confirmation string.

    OCP: replace with a real API/DB implementation by providing another
    ``IAppointmentRepository`` — no changes to the tool or LLM needed.
    LSP: any subclass/instance can be substituted wherever the interface
    is expected.
    """

    async def book(self, patient_name: str, appointment_date: str, appointment_time: str) -> str:
        """Log and confirm a booking; called by the appointment handler after validation."""
        name = patient_name.strip()
        date = appointment_date.strip()
        time = appointment_time.strip()
        print("[appointment] booking appointment")
        print(f"[appointment] patient: {name}")
        print(f"[appointment] date: {date}")
        print(f"[appointment] time: {time}")
        # TODO: Replace this with your actual booking API or database call.
        print("call the api for appointment")
        return f"Appointment successfully booked for {name} on {date} at {time}."


class StrictAppointmentValidator(IAppointmentValidator):
    """SRP: only decides which required fields are missing."""

    def __init__(self, required: tuple[str, ...] = REQUIRED_FIELDS):
        """Set required booking fields; used when constructing the booking handler."""
        self._required = required

    def missing_fields(
        self,
        patient_name: Optional[str],
        appointment_date: Optional[str],
        appointment_time: Optional[str],
    ) -> list[str]:
        """Return empty or missing field names; called before the repository books an appointment."""
        values = {
            "patient_name": patient_name,
            "appointment_date": appointment_date,
            "appointment_time": appointment_time,
        }
        return [
            field
            for field in self._required
            if not (values.get(field) or "").strip()
        ]


_DEFAULT_REPOSITORY = LoggingAppointmentRepository()
_DEFAULT_VALIDATOR = StrictAppointmentValidator()


def get_default_repository() -> IAppointmentRepository:
    """Return the shared booking repository; used by the default appointment handler."""
    return _DEFAULT_REPOSITORY


def get_default_validator() -> IAppointmentValidator:
    """Return the shared field validator; used by the default appointment handler."""
    return _DEFAULT_VALIDATOR
