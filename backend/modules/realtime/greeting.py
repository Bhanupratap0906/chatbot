"""Greeting use-case (SRP).

Speaks one fixed welcome message. Depends only on the tiny
``MessagePublisher`` + ``AsyncSpeaker`` + ``ConversationHistory``
interfaces (DIP/ISP), so it is unit-testable without websockets.
"""

from __future__ import annotations

import time

from modules.common.protocols import AsyncSpeaker, ConversationHistory, MessagePublisher


class GreetingService:
    def __init__(
        self,
        publisher: MessagePublisher,
        speaker: AsyncSpeaker,
        history: ConversationHistory,
        greeting_text: str,
        voice: str,
    ):
        self._publisher = publisher
        self._speaker = speaker
        self._history = history
        self._text = greeting_text
        self._voice = voice

    async def send(self) -> None:
        started = time.perf_counter()

        def elapsed():
            return f"{time.perf_counter() - started:.2f}s"

        try:
            print("[turn] greeting")
            await self._publisher.send_json({"type": "answer_start"})
            await self._publisher.send_json({"type": "token", "text": self._text})
            await self._speaker.speak(self._text, self._voice)
            self._history.add_ai(self._text)
            await self._publisher.send_json({"type": "answer_end"})
            print(f"[turn] greeting done at {elapsed()}")
        except Exception as error:
            print(f"[turn] greeting failed at {elapsed()}: {error!r}")
            try:
                await self._publisher.send_json({"type": "answer_end"})
            except Exception:
                pass
