"""Regression tests for caller-owned booking confirmation state."""

from __future__ import annotations

from unittest.mock import AsyncMock

from modules.common.booking_state import BookingState, set_booking_state
from modules.tools.appointment_booking_tool import create_appointment_booking_handler
from modules.tools.fallback_appointment_repository import get_default_validator
from modules.workflow.conversation_workflow_graph import (
    ConversationWorkflow,
    WorkflowState,
    _looks_like_booking_readback,
)
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from tests.fake_llm import FakeLLM


def test_state_records_only_an_offered_caller_selection() -> None:
    state = BookingState()
    state.replace_offered_slots(["8:00 AM", "8:35 AM"])

    state.record_caller_utterance("Ten AM works")
    assert state.selected_slot is None

    state.record_caller_utterance("8:35 AM works")
    assert state.selected_slot == "08:35"


def test_state_confirms_only_after_readback() -> None:
    state = BookingState()
    state.record_caller_utterance("Yes, exactly")
    assert state.user_confirmed is False

    state.readback_done = True
    state.record_caller_utterance("Yes, exactly")
    assert state.user_confirmed is True


def test_collection_state_follows_spoken_questions() -> None:
    state = BookingState(appointment_date_provided=True)
    state.record_assistant_utterance("What's your full name?")
    state.record_caller_utterance("David Johnson")
    assert state.first_missing_information() == "date of birth"

    state.record_assistant_utterance("When were you born?")
    state.record_caller_utterance("June 9th, 2001")
    assert state.first_missing_information() == "mobile number"


def test_readback_detection_requires_confirmation_and_time() -> None:
    assert _looks_like_booking_readback(
        "To confirm: David Johnson, October 30th at 8:35 AM. Is that correct?"
    )
    assert not _looks_like_booking_readback("What gender do you identify as?")


async def test_cut_off_utterance_does_not_call_llm_or_tools() -> None:
    llm = FakeLLM(reply="This response must not be used")
    workflow = ConversationWorkflow(llm=llm)

    pieces = [
        piece
        async for piece in workflow.stream_conversation_turn(
            "My name is David Johnson, and"
        )
    ]

    assert pieces == ["Go ahead."]
    assert llm.calls == []


async def test_availability_is_blocked_until_caller_fields_are_collected() -> None:
    workflow = ConversationWorkflow(llm=FakeLLM(reply="unused"))
    workflow.booking_state.patient_name_provided = True
    workflow._turn_id = 1
    response = AIMessage(
        content="Let me check.",
        tool_calls=[
            {
                "id": "slots-1",
                "name": "find_available_slots",
                "args": {"appointment_date": "2026-10-30", "service": None},
            }
        ],
    )
    state: WorkflowState = {
        "messages": [HumanMessage(content="David Johnson"), response],
        "turn_text": "David Johnson",
        "turn_id": 1,
        "assigned_agent": "appointments",
    }

    result = await workflow._tools(state)

    replies = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert len(replies) == 1
    assert "date of birth" in str(replies[0].content)


async def test_confirmed_selected_slot_reaches_repository() -> None:
    repository = AsyncMock()
    repository.book.return_value = "Appointment booked."
    state = BookingState(
        readback_done=True,
        user_confirmed=True,
        offered_slots={"08:35"},
        selected_slot="08:35",
    )
    set_booking_state(state)
    handler = create_appointment_booking_handler(
        repository=repository,
        validator=get_default_validator(),
    )

    result = await handler(
        patient_name="David Johnson",
        birth_date="2001-06-09",
        mobile_number="7378179106",
        gender="male",
        appointment_date="2026-12-01",
        appointment_time="8:35 AM",
        service="tooth pain",
        confirmed_by_patient=True,
    )

    assert result == "Appointment booked."
    repository.book.assert_awaited_once()


async def test_unselected_slot_never_reaches_repository() -> None:
    repository = AsyncMock()
    state = BookingState(
        readback_done=True,
        user_confirmed=True,
        offered_slots={"08:00", "08:35"},
    )
    set_booking_state(state)
    handler = create_appointment_booking_handler(
        repository=repository,
        validator=get_default_validator(),
    )

    result = await handler(
        patient_name="David Johnson",
        birth_date="2001-06-09",
        mobile_number="7378179106",
        gender="male",
        appointment_date="2026-12-01",
        appointment_time="8:00 AM",
        service="tooth pain",
        confirmed_by_patient=True,
    )

    assert "choose one of the offered times" in result
    repository.book.assert_not_awaited()
