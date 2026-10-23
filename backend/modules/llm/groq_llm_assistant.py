"""Groq LLM assistant (SRP + DIP + OCP + LSP).

* ``GroqLlmAssistant`` implements ``LlmAssistant`` and only orchestrates:
  guardrail -> tool-check -> plain stream | tool execute -> final stream.
* Guardrail, tool executor, model client and system prompt are injected
  (DIP), so each can be faked/substituted (LSP) and new tools added via
  the registry without editing this file (OCP).
* Chunking, error classification and message formatting live in their own
  modules (SRP).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Mapping
from typing import Optional

from langchain_core.messages import AIMessage
from langchain_groq import ChatGroq

from modules.common.conversation_cost_tracker import ConversationCostTracker
from modules.common.backend_contracts import Guardrail, LlmAssistant
from modules.common.settings import get_settings
from modules.llm.llm_error_classification import is_tool_validation_error
from modules.llm.prompt_injection_guardrail import RegexInjectionGuardrail
from modules.llm.llm_tool_call_executor import LlmToolCallExecutor

logger = logging.getLogger("llm")


SYSTEM_PROMPT = """You are a voice receptionist for a dental clinic. Replies are spoken aloud.

STYLE
- 1-2 short sentences per turn. Never use lists, numbering, markdown or formats like "YYYY-MM-DD".
- Ask ONE question at a time. Say dates naturally ("October 28th").
- Don't repeat what the caller said. Don't over-empathize: one short phrase, then move on.

CAPABILITIES
- You CAN book appointments using your tools. Never say you can't book, and never send callers to the website or front desk unless a tool fails.
- Don't say "let me check" unless you are calling a tool in that same turn. After a tool returns, always use its result in your reply.

BOOKING FLOW
1. If the caller mentions pain or a problem, ask once: "Is there swelling, fever, or trouble swallowing or breathing?"
   If yes, tell them to seek urgent care or the ER now. If no, continue.
2. Offer to book the earliest slot. Map the reason to the closest available reason in the clinic data (e.g. toothache -> exam/filling); don't ask the caller to pick.
3. Collect one at a time: full name -> date of birth -> mobile number (offer the caller ID number first) -> gender -> preferred day/time.
4. Call the availability tool, offer at most 2 slots, confirm one.
5. Book, then read back: name, provider, date, time. Ask if there's anything else.

RULES
- If the caller already said "I want to book", skip small talk and go straight to step 1 or 3.
- If the caller's sentence seems unfinished, wait; don't reply to fragments.
- If the caller is silent for ~6 seconds, say "Are you still there?"
- Never give medical diagnoses. Say a dentist will examine them.
- Never say an appointment is booked or scheduled unless book_appointment returned success this turn.
- Use the exact phone number the caller provides; never substitute a placeholder or example number.
- Never defer to a scheduling team or say you will call back."""


OUT_OF_SCOPE_REPLY = (
    "I'm here to help with dental clinic questions "
    "and appointments. How can I help you?"
)


def _normalize_message_content_to_text(content) -> str:
    """Normalize provider message content into a string; used for streamed LLM chunks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                parts.append(block.get("text", ""))
        return "".join(parts)
    return str(content or "")


async def _stream_plain_llm_response(llm, messages, **kwargs):
    """Stream a normal (no-tool) reply."""
    async for chunk in llm.astream(messages, **kwargs):
        piece = _normalize_message_content_to_text(chunk.content)
        if piece:
            yield piece


class _UsageCapturingAsyncClient:
    """Forward Groq streaming responses while retaining usage-only chunks."""

    def __init__(self, client, on_usage):
        self._client = client
        self._on_usage = on_usage

    async def create(self, *args, **kwargs):
        # AsyncGroq's stream=True create() is awaitable and resolves to an
        # async iterator. Some compatible clients return the iterator directly.
        stream = self._client.create(*args, **kwargs)
        if hasattr(stream, "__await__"):
            stream = await stream

        if not hasattr(stream, "__aiter__"):
            return stream

        async def with_usage():
            async for chunk in stream:
                if isinstance(chunk, Mapping):
                    data = chunk
                elif hasattr(chunk, "model_dump"):
                    data = chunk.model_dump()
                else:
                    data = {}
                usage = data.get("usage")
                if usage:
                    self._on_usage(usage)
                yield chunk

        return with_usage()


class GroqLlmAssistant(LlmAssistant):
    """Apply guardrails and tool handling while streaming model responses."""

    def __init__(
        self,
        model: str | None = None,
        llm=None,
        guardrail: Guardrail | None = None,
        tool_executor: LlmToolCallExecutor | None = None,
        system_prompt: str = SYSTEM_PROMPT,
        cost_tracker: ConversationCostTracker | None = None,
    ):
        """Configure the model, system prompt, guardrail, and registered tools."""
        settings = get_settings()
        self.model = model or settings.groq_model
        self.system_prompt = system_prompt
        self.cost_tracker = cost_tracker
        self.guardrail = guardrail or RegexInjectionGuardrail()
        self.tool_executor = tool_executor or LlmToolCallExecutor()
        self.llm = llm or ChatGroq(
            model=self.model,
            temperature=0,
            max_tokens=settings.llm_max_tokens,
            max_retries=0,
            timeout=15,
            reasoning_format="hidden",
            reasoning_effort="low",
        )
        # OCP: bind whatever the registry currently holds.
        self.llm_with_tools = self.llm.bind_tools(self.tool_executor.tools)
        if cost_tracker is not None and isinstance(self.llm, ChatGroq):
            original_client = self.llm.async_client
            object.__setattr__(
                self.llm,
                "async_client",
                _UsageCapturingAsyncClient(
                    original_client,
                    lambda usage: cost_tracker.add_llm_usage(usage, self.model),
                ),
            )
        logger.info("using model=%s", self.model)

    def _messages(self, text: str, history=None) -> list:
        """Build the system/history/user message list for one caller turn."""
        messages = [("system", self.system_prompt)]
        messages += history or []
        messages.append(("human", text))
        return messages

    async def stream(self, text: str, history: Optional[list] = None) -> AsyncIterator[str]:
        """Yield the assistant response; called by AssistantTurnManager for every recognized utterance."""
        if not self.guardrail.is_allowed(text):
            yield OUT_OF_SCOPE_REPLY
            return

        messages = self._messages(text, history)

        try:
            response_chunk = None
            yielded_any = False
            try:
                # Stream the tool-bound request itself. The previous flow first
                # invoked the model to check for tools, then made a second full
                # request to stream ordinary answers.
                async for chunk in self.llm_with_tools.astream(messages):
                    response_chunk = chunk if response_chunk is None else response_chunk + chunk
                    piece = _normalize_message_content_to_text(chunk.content)
                    if piece:
                        yielded_any = True
                        yield piece
            except Exception as error:
                if is_tool_validation_error(error):
                    logger.warning(
                        "provider rejected the tool call (%r) — falling back to a normal reply",
                        error,
                    )
                    async for piece in _stream_plain_llm_response(self.llm, messages):
                        yield piece
                    return
                raise

            if response_chunk is None or not response_chunk.tool_calls:
                return

            if not yielded_any:
                tool_names = {str(call.get("name", "")) for call in (response_chunk.tool_calls or [])}
                if "book_appointment" in tool_names:
                    yield "Let me book that appointment for you now."
                elif tool_names & {"find_available_slots", "list_services", "list_providers"}:
                    yield "Let me check our schedule and services for you."
                elif "find_patient_by_phone" in tool_names:
                    yield "Let me look up your record."
                else:
                    yield "Let me check that for you."

            response = AIMessage(
                content=response_chunk.content,
                tool_calls=response_chunk.tool_calls,
                additional_kwargs=response_chunk.additional_kwargs,
                response_metadata=response_chunk.response_metadata,
            )
            messages.append(response)
            await self.tool_executor.execute_tool_calls(response, messages)

            async for chunk in self.llm.astream(messages):
                piece = _normalize_message_content_to_text(chunk.content)
                if piece:
                    yield piece

        except Exception as error:
            logger.warning("model=%s failed: %r", self.model, error)
            raise
