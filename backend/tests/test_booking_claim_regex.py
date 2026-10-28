"""Regression test for item 2: the claims_booking regex never matched.

The pattern was written with r"\\b" (escaped backslash), which matches a
literal backslash followed by 'b' instead of a word boundary, so a model
claiming a booking was spoken to the caller even when book_appointment never
ran. With the correct r"\b" pattern each claimed booking must be replaced by
the not-booked correction.
"""

from __future__ import annotations

import pytest

from modules.workflow.conversation_workflow_graph import ConversationWorkflow
from tests.fake_llm import FakeLLM

CORRECTION = "I haven't booked that yet. Let me confirm the details with you first."


@pytest.mark.parametrize(
    "claim",
    [
        "Your appointment is booked.",
        "I've booked it for you.",
        "You are confirmed for tomorrow.",
    ],
)
async def test_claimed_booking_is_corrected(claim: str):
    workflow = ConversationWorkflow(llm=FakeLLM(reply=claim))

    pieces = [piece async for piece in workflow.stream_conversation_turn("hello there")]

    assert pieces == [CORRECTION]


async def test_genuine_reply_is_not_corrected():
    reply = "Hi! How can I help with your dental clinic visit today?"
    workflow = ConversationWorkflow(llm=FakeLLM(reply=reply))

    pieces = [piece async for piece in workflow.stream_conversation_turn("hello there")]

    assert pieces == [reply]
