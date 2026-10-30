"""Item 20 part 2: Deepgram reconnect and fallback."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.realtime.realtime_voice_session import RealtimeVoiceSession


class _FailingDG:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise ConnectionError("socket dropped")


async def test_speak_fallback_and_escalate_sets_hangup() -> None:
    session = RealtimeVoiceSession.__new__(RealtimeVoiceSession)
    # minimal init
    session._settings = MagicMock()
    session._settings.barge_in = False
    session._settings.input_sample_rate = 16000
    session._settings.deepgram_api_key = "k"
    session.voice = "v"
    session._buffer = MagicMock()
    session._publisher = MagicMock()
    session._publisher.send_json = AsyncMock()
    session._publisher.send_bytes = AsyncMock()
    session.tts = MagicMock()
    session.tts.speak = AsyncMock()
    session.turns = MagicMock()
    session.turns._hangup = False
    session.hangup_requested = False
    session.dg = _FailingDG()

    await session._speak_fallback_and_escalate()

    assert session.turns._hangup is True
    assert session.hangup_requested is True
    session.tts.speak.assert_awaited()
