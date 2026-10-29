"""Rate-limit retry helpers and history-trim guards for agent LLM requests."""
from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from modules.workflow.conversation_agents import (
    AGENT_ROLES,
    _MAX_CONVERSATION_MESSAGES,
    _trim_conversation,
    messages_for_agent,
)
from modules.workflow.conversation_workflow_graph import (
    _is_rate_limit_error,
    _rate_limit_delay_seconds,
)


class _GroqLikeRateLimit(Exception):
    """Shaped like Groq's RateLimitError message so the parser has real input."""

    def __init__(self, seconds: str) -> None:
        super().__init__(
            f"Rate limit reached for openai/gpt-oss-20b. Please try again in {seconds}s."
        )
        self.status_code = 429


def test_rate_limit_error_detection() -> None:
    assert _is_rate_limit_error(_GroqLikeRateLimit("1.32"))
    assert _is_rate_limit_error(__import__("builtins").Exception("Rate limit exceeded"))
    assert not _is_rate_limit_error(ValueError("boom"))


def test_retry_delay_is_clamped_for_voice() -> None:
    assert _rate_limit_delay_seconds(_GroqLikeRateLimit("1.32")) == 1.32
    # Gaps up to 8s are worth waiting out once; 12s would dead-air the call.
    assert _rate_limit_delay_seconds(_GroqLikeRateLimit("5.5")) == 5.5
    assert _rate_limit_delay_seconds(_GroqLikeRateLimit("8.0")) == 8.0
    assert _rate_limit_delay_seconds(_GroqLikeRateLimit("12.5")) == 8.0
    # A 0.2s wait is too short to help; clamp high.
    assert _rate_limit_delay_seconds(_GroqLikeRateLimit("0.2")) == 1.0
    assert _rate_limit_delay_seconds(ValueError("boom")) == 3.0


def test_trim_keeps_short_history_untouched() -> None:
    conversation = [HumanMessage(content="hi")]
    assert _trim_conversation(conversation) is conversation


def test_trim_never_orphans_tool_results() -> None:
    conversation: list = []
    for index in range(9):
        conversation.append(HumanMessage(content=f"msg {index}"))
        conversation.append(AIMessage(content=f"answer {index}"))
    # Tool block whose AIMessage would fall outside a naive last-12 slice.
    conversation.append(
        AIMessage(
            content="",
            tool_calls=[{"id": "t1", "name": "find_available_slots", "args": {}}],
        )
    )
    conversation.append(ToolMessage(content="slots 8:00", tool_call_id="t1"))
    trimmed = _trim_conversation(conversation)
    assert len(trimmed) <= _MAX_CONVERSATION_MESSAGES
    assert trimmed[-2].tool_calls, "tool-call AIMessage must survive the trim"
    assert isinstance(trimmed[-1], ToolMessage)


def test_trim_drops_orphan_tool_messages() -> None:
    conversation: list = []
    for index in range(12):
        conversation.append(
            ToolMessage(content=f"orphan {index}", tool_call_id=f"orphan-{index}")
        )
    conversation.append(HumanMessage(content="hello"))
    trimmed = _trim_conversation(conversation)
    assert [type(message) for message in trimmed] == [HumanMessage]


def test_messages_for_agent_uses_trimmed_history() -> None:
    role = AGENT_ROLES["appointments"]
    conversation: list = []
    for index in range(20):
        conversation.append(HumanMessage(content=f"msg {index}"))
        conversation.append(AIMessage(content=f"answer {index}"))
    messages = messages_for_agent(role, conversation)
    rendered = [str(message.content) for message in messages]
    # System prompt first, then at most 12 historical messages, then the
    # fixed tail advisories appended by messages_for_agent itself.
    assert len(messages) > _MAX_CONVERSATION_MESSAGES
    assert "voice receptionist" in rendered[0]
    nuclei = [m for m in messages[1:] if isinstance(m, (HumanMessage, AIMessage))]
    assert len(nuclei) <= _MAX_CONVERSATION_MESSAGES
    assert any(SystemMessage == type(m) for m in messages)
