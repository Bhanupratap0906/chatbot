"""Greeting use-case (SRP).

Speaks one fixed welcome message. Depends only on the tiny
``MessagePublisher`` + ``AsyncSpeaker`` + ``ConversationHistory``
interfaces (DIP/ISP), so it is unit-testable without websockets.
"""

from __future__ import annotations

import logging
import time

from modules.common.backend_contracts import AsyncSpeaker, ConversationHistory, MessagePublisher

logger = logging.getLogger("greeting")


class GreetingService:
    """Send the configured welcome text and speech once at call start."""

    def __init__(
        self,
        publisher: MessagePublisher,
        speaker: AsyncSpeaker,
        history: ConversationHistory,
        greeting_text: str,
        voice: str,
    ):
        """Store the socket publisher, speaker, history, greeting, and selected voice."""
        self._publisher = publisher
        self._speaker = speaker
        self._history = history
        self._text = greeting_text
        self._voice = voice

    async def send(self) -> None:
        """Publish and speak the greeting; launched by the websocket session setup."""
        started = time.perf_counter()

        def elapsed():
            """Format elapsed greeting time for diagnostic logs."""
            return f"{time.perf_counter() - started:.2f}s"

        try:
            logger.info("greeting start")
            await self._publisher.send_json({"type": "answer_start"})
            await self._publisher.send_json({"type": "token", "text": self._text})
            await self._speaker.speak(self._text, self._voice)
            self._history.add_ai(self._text)
            await self._publisher.send_json({"type": "answer_end"})
            logger.info("greeting done at %s", elapsed())
        except Exception as error:
            logger.warning("greeting failed at %s: %r", elapsed(), error)
            try:
                await self._publisher.send_json({"type": "answer_end"})
            except Exception:
                pass
