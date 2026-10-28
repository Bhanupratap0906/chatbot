"""Item 18: deterministic emergency triage."""

from __future__ import annotations

import pytest

from modules.common.emergency import is_emergency, emergency_instruction
from modules.workflow.conversation_workflow_graph import ConversationWorkflow
from tests.fake_llm import FakeLLM


@pytest.mark.parametrize("text,expected", [
    ("I have swelling and a fever", True),
    ("my face is swollen and I have a fever", True),
    ("I'm having trouble breathing", True),
    ("I can't swallow", True),
    ("bleeding that won't stop", True),
    ("no swelling and no fever", False),
    ("no, nothing like that", False),
    ("no trouble breathing", False),
    ("no fever but I do have a toothache", False),
    ("I don't have any swelling", False),
])
def test_emergency_table(text: str, expected: bool) -> None:
    assert is_emergency(text) == expected


def test_is_emergency_detects_phrases() -> None:
    assert is_emergency("I have swelling with fever")
    assert is_emergency("trouble breathing")
    assert is_emergency("uncontrolled bleeding")
    assert not is_emergency("I have a toothache")


async def test_emergency_skips_llm_and_escalates() -> None:
    llm = FakeLLM(reply="should not be called")
    workflow = ConversationWorkflow(llm=llm)
    pieces = [p async for p in workflow.stream_conversation_turn("I have swelling with fever")]
    assert any("emergency" in p.lower() or "urgent" in p.lower() for p in pieces)
    assert "HANGUP_CALL" in pieces
    assert not llm.calls  # LLM never invoked
