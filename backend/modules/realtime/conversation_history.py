"""Conversation history value object (SRP).

Owns the ``[(role, text)]`` list and the windowing policy. The turn loop
only calls ``snapshot / add_turn / add_ai`` — it never slices lists
itself (SRP). Window size is injectable (OCP).
"""

from __future__ import annotations

from modules.common.backend_contracts import ConversationHistory as IConversationHistory


class InMemoryConversationHistory(IConversationHistory):
    """Keep a bounded conversation history for prompts; used by each live RealtimeVoiceSession."""

    def __init__(self, window: int = 12):
        """Create an empty history retaining at most ``window`` messages."""
        self._window = window
        self._items: list = []

    def snapshot(self) -> list:
        """Return a copy of recent messages; called when building an LLM prompt."""
        return list(self._items)

    def add_turn(self, user_text: str, ai_text: str) -> None:
        """Append the caller and assistant messages; called when an LLM turn completes."""
        self._items.append(("human", user_text))
        self._items.append(("ai", ai_text))
        self._items = self._items[-self._window:]

    def add_ai(self, text: str) -> None:
        """Append assistant-only text; used to record the opening greeting."""
        self._items.append(("ai", text))
        self._items = self._items[-self._window:]

    def replace_snapshot(self, messages: list) -> None:
        """Replace turn history from a LangGraph conversation checkpoint."""
        self._items = [
            ("human" if getattr(message, "type", "") == "human" else "ai", message.content)
            for message in messages
            if getattr(message, "type", "") in ("human", "ai")
        ][-self._window:]
