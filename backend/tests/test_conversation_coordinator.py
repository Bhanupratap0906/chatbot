"""Tests for the two-agent conversation coordinator."""

import asyncio
import pytest

from modules.realtime.conversation_coordinator import (
    ConversationCoordinator,
    BookingState,
    BookingResult,
)


@pytest.mark.asyncio
async def test_coordinator_start_booking():
    coord = ConversationCoordinator(session_id="test-01")
    op_id = await coord.start_booking({"date": "2026-10-29", "service": "Filling"})
    assert op_id != ""
    assert coord.state.booking_status == BookingState.BOOKING_IN_PROGRESS
    assert coord.state.pending_appointment_details is not None


@pytest.mark.asyncio
async def test_utterance_during_booking():
    coord = ConversationCoordinator(session_id="test-02")
    await coord.start_booking({"date": "2026-10-29"})
    await coord.receive_utterance("Is my appointment booked yet?")
    assert coord.state.latest_patient_utterance == "Is my appointment booked yet?"
    # Status should acknowledge without creating duplicate work
    status_msg = await coord.get_status_for_speech()
    assert "still being processed" in status_msg or "appointment" in status_msg


@pytest.mark.asyncio
async def test_confirmation_routing():
    coord = ConversationCoordinator(session_id="test-03")
    await coord.start_booking({"date": "2026-10-29", "time": "08:00"})
    # After read-back, booking agent updates status to awaiting confirmation
    coord.state.booking_status = BookingState.AWAITING_PATIENT_CONFIRMATION
    await coord.receive_utterance("Yes, confirm it.")
    result = await coord.confirm_pending_booking()
    assert result is True
    assert coord.state.confirmation_status is True


@pytest.mark.asyncio
async def test_cancellation():
    coord = ConversationCoordinator(session_id="test-04")
    await coord.start_booking({"date": "2026-10-29"})
    cancelled = await coord.cancel_pending_booking()
    assert cancelled is True
    assert coord.state.booking_status == BookingState.CANCELLED
    assert coord.state.cancellation_status is True


@pytest.mark.asyncio
async def test_result_delivery():
    coord = ConversationCoordinator(session_id="test-05")
    await coord.start_booking({"date": "2026-10-29"})
    await coord.deliver_result(
        BookingResult(
            operation_id=coord.state.current_booking_operation_id,
            status=BookingState.BOOKING_SUCCEEDED,
            message="Appointment booked for 2026-10-29 at 08:00.",
            appointment_details={"date": "2026-10-29", "time": "08:00"},
            is_terminal=True,
        )
    )
    assert coord.state.booking_status == BookingState.BOOKING_SUCCEEDED
    assert coord.state.latest_booking_result is not None
    assert "booked" in coord.state.latest_booking_result.message


@pytest.mark.asyncio
async def test_stale_result_ignored():
    coord = ConversationCoordinator(session_id="test-06")
    await coord.start_booking({"date": "2026-10-29"})
    await coord.deliver_result(
        BookingResult(
            operation_id="stale-id",
            status=BookingState.BOOKING_SUCCEEDED,
            message="Old result",
        )
    )
    assert coord.state.latest_booking_result is None
    assert coord.state.booking_status == BookingState.BOOKING_IN_PROGRESS


@pytest.mark.asyncio
async def test_session_isolation():
    coord1 = ConversationCoordinator(session_id="test-isolate-01")
    coord2 = ConversationCoordinator(session_id="test-isolate-02")
    await coord1.start_booking({"date": "2026-10-29"})
    await coord2.start_booking({"date": "2026-11-01"})
    assert coord1.state.current_booking_operation_id != coord2.state.current_booking_operation_id
    await coord1.deliver_result(
        BookingResult(
            operation_id=coord1.state.current_booking_operation_id,
            status=BookingState.BOOKING_SUCCEEDED,
        )
    )
    assert coord1.state.booking_status == BookingState.BOOKING_SUCCEEDED
    assert coord2.state.booking_status == BookingState.BOOKING_IN_PROGRESS
