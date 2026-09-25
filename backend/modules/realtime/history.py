"""Conversation history value object (SRP).

Owns the ``[(role, text)]`` list and the windowing policy. The turn loop
only calls ``snapshot / add_turn / add_ai`` — it never slices lists
itself (SRP). Window size is injectable (OCP).
"""

from __future__ import annotations

from modules.common.protocols import ConversationHistory as IConversationHistory


class InMemoryHistory(IConversationHistory):
    def __init__(self, window: int = 12):
        self._window = window
        self._items: list = []

    def snapshot(self) -> list:
        return list(self._items)

    def add_turn(self, user_text: str, ai_text: str) -> None:
        self._items.append(("human", user_text))
        self._items.append(("ai", ai_text))
        self._items = self._items[-self._window:]

    def add_ai(self, text: str) -> None:
        self._items.append(("ai", text))
        self._items = self._items[-self._window:]
