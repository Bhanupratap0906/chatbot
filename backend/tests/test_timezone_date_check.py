"""Item 17: date checks use settings.clinic_timezone, not server time."""

from __future__ import annotations

from datetime import datetime as real_datetime, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from modules.common.settings import get_settings
from modules.common.booking_state import BookingState, set_booking_state
from modules.tools.appointment_booking_tool import create_appointment_booking_handler
from modules.tools.fallback_appointment_repository import (
    get_default_repository,
    get_default_validator,
)


async def test_appointment_date_check_uses_clinic_timezone() -> None:
    get_settings.cache_clear()
    # 20:00 UTC == 01:30 next day in Kolkata.
    clinic_now = real_datetime(2026, 1, 2, 1, 30, 0, tzinfo=ZoneInfo("Asia/Kolkata"))

    handler = create_appointment_booking_handler(
        repository=get_default_repository(),
        validator=get_default_validator(),
    )
    booking_state = BookingState(
        readback_done=True,
        user_confirmed=True,
        offered_slots={"10:00"},
        selected_slot="10:00",
    )
    set_booking_state(booking_state)

    with patch("modules.tools.appointment_booking_tool.datetime") as mock_dt:
        mock_dt.now.return_value = clinic_now
        mock_dt.strptime = real_datetime.strptime
        # 2026-01-01 is "today" in UTC but already yesterday in Kolkata.
        result = await handler(
            patient_name="John Doe",
            appointment_date="2026-01-01",
            appointment_time="10:00 AM",
            mobile_number="9999999999",
                confirmed_by_patient=True,
                service="cleaning",
                birth_date="1990-01-01",
                gender="male",
            )

    assert "today or later" in result
