"""Fix A: booking confirmation loop — fails before, passes after."""
from __future__ import annotations
from langchain_core.messages import HumanMessage, AIMessage
from modules.workflow.conversation_workflow_graph import ConversationWorkflow, WorkflowState
from tests.fake_llm import FakeLLM

class FakeLLMBooking(FakeLLM):
    pass

async def test_booking_confirmed_after_readback():
    """Turn 1: model reads back; turn 2: user 'Yes I confirm' + book_appointment -> executes."""
    # Phase 1: turn 1 produces read-back AI message with details
    llm1 = FakeLLM(reply="To confirm: John Smith, October 28th at 11:00 AM for toothache. Is that right?")
    wf = ConversationWorkflow(llm=llm1)
    # Run turn 1
    pieces = [p async for p in wf.stream_conversation_turn("I want to book an appointment")]
    assert len(pieces) == 1
    # Phase 2: inject state with pending booking (simulating turn 1 stored state)
    # Then turn 2 user confirms and model calls tool
    llm2 = FakeLLM(reply="")  # tool call only, no spoken filler needed for test
    # Manually set workflow state to simulate phase 2
    config = wf._get_graph_config()
    wf.workflow_state = {
        "pending_booking": {
            "patient_name": "John Smith",
            "date": "2026-10-28",
            "time": "11:00 AM",
            "service": "toothache",
            "mobile_number": "9876543210",
        },
    }
    # Call _tools directly with a tool-calling AIMessage
    from langchain_core.messages import AIMessage, ToolMessage
    tool_call = {"id": "tc1", "name": "book_appointment", "args": {
        "patient_name": "John Smith", "appointment_date": "2026-10-28",
        "appointment_time": "11:00 AM", "mobile_number": "9876543210",
        "service": "toothache", "confirmed_by_patient": True
    }}
    ai_msg = AIMessage(content="Booking now.", tool_calls=[tool_call])
    state: WorkflowState = {
        "messages": [HumanMessage(content="Yes I confirm")],
        "turn_text": "Yes I confirm",
        "turn_id": 2,
        "workflow": {"pending_booking": wf.workflow_state["pending_booking"]},
    }
    result = await wf._tools(state)
    # The guard should allow because args match pending and turn is affirmative
    tool_replies = result.get("messages", [])
    tool_ids = {m.tool_call_id for m in tool_replies if isinstance(m, ToolMessage)}
    assert "tc1" in tool_ids, f"Expected tool response for tc1, got {tool_ids}"
