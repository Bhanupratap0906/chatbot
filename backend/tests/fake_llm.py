"""Shared fake LLM for workflow tests.

``import main`` never constructs the workflow and the tests must not touch
the network, so every test drives ``ConversationWorkflow`` through this
stand-in: it exposes ``bind_tools()`` and ``ainvoke()`` returning an
``AIMessage``, the two hooks the workflow uses.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage


class FakeLLM:
    """Minimal LLM double: records binds and calls, always returns one reply."""

    def __init__(self, reply: str):
        self.reply = reply
        self.calls: list[list] = []
        self.bound: list[list] = []

    def bind_tools(self, tools):
        self.bound.append(list(tools))
        return self

    async def ainvoke(self, messages, *args, **kwargs):
        self.calls.append(list(messages))
        return AIMessage(content=self.reply)
