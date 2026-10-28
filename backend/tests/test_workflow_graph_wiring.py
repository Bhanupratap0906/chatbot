"""Regression test for item 1 (part one): the graph wiring.

``_build_graph`` used to wire nonexistent methods (_generate_agent_response,
_execute_requested_tools, _finalize_turn_response, _handle_tool_limit_reached,
_route_after_agent_response), so constructing ConversationWorkflow raised
AttributeError. It must wire the real methods (_respond, _tools, _record_turn,
_tool_limit, _route_after_response); this test builds the workflow with a fake
LLM and runs one full turn through the graph.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage

from modules.workflow.conversation_workflow_graph import ConversationWorkflow
from tests.fake_llm import FakeLLM

REPLY = "Hi! How can I help with your dental clinic visit today?"


async def test_one_turn_end_to_end_with_fake_llm():
    llm = FakeLLM(reply=REPLY)
    workflow = ConversationWorkflow(llm=llm)

    pieces = [piece async for piece in workflow.stream_conversation_turn("hello there")]

    # respond -> (no tool calls) route_after_response says done -> record_turn -> END.
    assert pieces == [REPLY]
    assert llm.bound, "the agent must bind its scoped tools before invoking the LLM"
    last_call = llm.calls[-1]
    assert isinstance(last_call[-1], HumanMessage) and last_call[-1].content == "hello there"


async def test_one_turn_records_state_in_checkpoint():
    llm = FakeLLM(reply=REPLY)
    workflow = ConversationWorkflow(llm=llm)

    async for _ in workflow.stream_conversation_turn("hello there"):
        pass

    state = await workflow._graph.aget_state(workflow._get_graph_config())
    assert state.values.get("response_text") == REPLY
    assert any(
        isinstance(message, AIMessage) and message.content == REPLY
        for message in state.values.get("messages", [])
    )
    assert not workflow.hangup_requested
