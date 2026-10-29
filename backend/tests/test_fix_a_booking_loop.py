"""Caller confirmation must NOT trigger a second re-confirmation phase.

After the caller affirms the read-back, a consistent book_appointment call
reaches the tool executor directly. Only genuinely inconsistent arguments
(wrong time, missing service) are returned to the model with a fix request.
"""
from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from modules.workflow.conversation_workflow_graph import ConversationWorkflow, WorkflowState
from tests.fake_llm import FakeLLM


def _confirmed_workflow() -> ConversationWorkflow:
    wf = ConversationWorkflow(llm=FakeLLM(reply="unused"))
    booking = wf.booking_state
    booking.patient_name_provided = True
    booking.birth_date_provided = True
    booking.mobile_number_provided = True
    booking.gender_provided = True
    booking.visit_reason_provided = True
    booking.appointment_date_provided = True
    booking.offered_slots = {"11:00", "11:35"}
    booking.selected_slot = "11:00"
    booking.readback_done = True
    booking.user_confirmed = True
    return wf


def _booking_args(appointment_time: str, service: str) -> dict:
    return {
        "patient_name": "David Johnson",
        "appointment_date": "2026-10-30",
        "appointment_time": appointment_time,
        "mobile_number": "7378179106",
        "service": service,
        "confirmed_by_patient": True,
    }


async def _run_tools(wf: ConversationWorkflow, tool_args: dict) -> dict:
    """Stub the executor so no portal call happens; assert the workflow gates only."""

    async def stub_execute(response, messages):
        for call in response.tool_calls:
            messages.append(
                ToolMessage(content="appointment booked successfully", tool_call_id=call["id"])
            )

    wf.tool_executor.execute_tool_calls = stub_execute

    ai_msg = AIMessage(
        content="",
        tool_calls=[{"id": "tc1", "name": "book_appointment", "args": tool_args}],
    )
    state: WorkflowState = {
        "messages": [HumanMessage(content="Yes, that's correct."), ai_msg],
        "turn_text": "Yes, that's correct.",
        "turn_id": 2,
        "assigned_agent": "appointments",
    }
    return await wf._tools(state)


async def test_confirmed_booking_reaches_executor_without_reconfirmation():
    wf = _confirmed_workflow()
    result = await _run_tools(wf, _booking_args("11:00 AM", "cavity filling"))
    replies = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert len(replies) == 1
    assert "appointment booked" in str(replies[0].content)
    assert result["tool_errors"].get("book_appointment") is None


async def test_wrong_time_argument_is_blocked_with_requested_time():
    wf = _confirmed_workflow()
    result = await _run_tools(wf, _booking_args("9:00 AM", "cavity filling"))
    replies = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert len(replies) == 1
    assert "Booking arguments inconsistent" in str(replies[0].content)
    assert "11:00" in str(replies[0].content)


async def test_missing_service_argument_is_blocked():
    wf = _confirmed_workflow()
    result = await _run_tools(wf, _booking_args("11:00 AM", ""))
    replies = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert len(replies) == 1
    assert "Booking arguments inconsistent" in str(replies[0].content)
    assert "problem or symptom" in str(replies[0].content)
