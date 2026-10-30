"""Items 15/16: booking safety in portal_appointment_booking_repository."""

from __future__ import annotations

from unittest.mock import AsyncMock

from modules.tools.portal_appointment_booking_repository import (
    PortalAppointmentBookingRepository,
)


def _client(**overrides):
    c = AsyncMock()
    c.get_patient_list_data = AsyncMock(return_value=overrides.get("patients", {"data": {"patients": []}}))
    c.get_procedure_master_list = AsyncMock(return_value=overrides.get("procs", {"data": [], "meta": {"totalCount": 0}}))
    c.get_appointment_master_data = AsyncMock(return_value=overrides.get("master", {"data": {"providers": [{"id": 1, "providerName": "Dr A"}], "reasons": []}}))
    c.get_appointment_slots = AsyncMock(return_value={"data": []})
    c.book_appointment = AsyncMock(return_value={"data": {"id": 99}})
    c.list_appointments = AsyncMock(return_value={"data": {"data": []}})
    c.create_patient = AsyncMock(return_value={"data": {"patientId": 7}})
    return c


async def test_full_name_required_not_subset() -> None:
    client = _client(patients={"data": {"patients": [
        {"id": 1, "displayName": "John Smith", "mobileNumber": "9999999999", "birthDate": "1990-01-01"},
    ]}})
    repo = PortalAppointmentBookingRepository(client=client, token_store=None)
    # inject a token store shim
    from modules.portal.token_store import portal_token_store
    portal_token_store.set("tok", location_id="loc1")
    res = await repo._find_patient_by_mobile_number("tok", "9999999999", "John", None)
    # "John" alone must not match "John Smith"
    assert res is None or (isinstance(res, dict) and res.get("ambiguous"))


async def test_dob_mismatch_rejects() -> None:
    client = _client(patients={"data": {"patients": [
        {"id": 1, "displayName": "John Smith", "mobileNumber": "9999999999", "birthDate": "1990-01-01"},
    ]}})
    repo = PortalAppointmentBookingRepository(client=client)
    from modules.portal.token_store import portal_token_store
    portal_token_store.set("tok", location_id="loc1")
    res = await repo._find_patient_by_mobile_number("tok", "9999999999", "John Smith", "1995-01-01")
    assert res is None


async def test_ambiguous_returns_marker() -> None:
    client = _client(patients={"data": {"patients": [
        {"id": 1, "displayName": "John Smith", "mobileNumber": "9999999999", "birthDate": "1990-01-01"},
        {"id": 2, "displayName": "John Smith", "mobileNumber": "9999999999", "birthDate": "1992-01-01"},
    ]}})
    repo = PortalAppointmentBookingRepository(client=client)
    from modules.portal.token_store import portal_token_store
    portal_token_store.set("tok", location_id="loc1")
    res = await repo._find_patient_by_mobile_number("tok", "9999999999", "John Smith", None)
    assert isinstance(res, dict) and res.get("ambiguous") is True


async def test_no_patient_last_name_placeholder() -> None:
    # single name -> should not auto-create with "Patient" last name
    client = _client(patients={"data": {"patients": []}})
    repo = PortalAppointmentBookingRepository(client=client)
    from modules.portal.token_store import portal_token_store
    portal_token_store.set("tok", location_id="loc1")
    msg = await repo.book(
        "John", "2026-12-01", "10:00 AM", "9999999999",
        birth_date="1990-01-01", gender="male", service="cleaning",
    )
    assert "full first and last name" in msg


async def test_period_returns_two_slots() -> None:
    client = _client(
        patients={"data": {"patients": [
            {"id": 1, "displayName": "John Smith", "mobileNumber": "9999999999", "birthDate": "1990-01-01"},
        ]}},
        procs={"data": [{"procedureId": 5, "procedureDescription": "Cleaning"}], "meta": {"totalCount": 1}},
    )
    # provide slots
    client.get_appointment_slots = AsyncMock(return_value={"data": [
        {"status": "open", "open": "2026-12-01T09:00:00"},
        {"status": "open", "open": "2026-12-01T10:30:00"},
        {"status": "open", "open": "2026-12-01T11:00:00"},
    ]})
    repo = PortalAppointmentBookingRepository(client=client)
    from modules.portal.token_store import portal_token_store
    portal_token_store.set("tok", location_id="loc1")
    msg = await repo.book(
        "John Smith", "2026-12-01", "morning", "9999999999",
        birth_date="1990-01-01", service="cleaning",
    )
    assert "Available times" in msg
    assert "9:00 AM" in msg or "10:30 AM" in msg


async def test_service_id_uses_all_items() -> None:
    # service only on page 2; all_items should find it
    client = _client(patients={"data": {"patients": []}})
    client.get_procedure_master_list = AsyncMock(side_effect=[
        {"data": [{"procedureId": 1, "procedureDescription": "X"}], "meta": {"totalCount": 25}},
        {"data": [{"procedureId": 2, "procedureDescription": "Cleaning"}], "meta": {"totalCount": 25}},
    ])
    repo = PortalAppointmentBookingRepository(client=client)
    from modules.portal.token_store import portal_token_store
    portal_token_store.set("tok", location_id="loc1")
    sid, name = await repo._resolve_service_id_from_patient_problem(client, "cleaning")
    assert sid == 2
