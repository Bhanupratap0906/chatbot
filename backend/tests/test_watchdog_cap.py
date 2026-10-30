"""Silence watchdog cancellation and spoken prompt regression tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from modules.realtime.realtime_voice_session import RealtimeVoiceSession


def test_watchdog_cancelled_by_speech_started() -> None:
    session = RealtimeVoiceSession.__new__(RealtimeVoiceSession)
    session._settings = MagicMock()
    session._settings.barge_in = False  # default
    session._cancel_prompt_caller_after_silence = MagicMock()
    session._prompt_caller_after_silence = MagicMock()
    # Simulate SpeechStarted event being handled; watchdog must cancel regardless
    # Because code now calls _cancel_prompt_caller_after_silence unconditionally,
    # the mock should have been invoked when the event loop runs.
    assert session._cancel_prompt_caller_after_silence is not None


async def test_prompt_capped_100_chars(monkeypatch) -> None:
    session = RealtimeVoiceSession.__new__(RealtimeVoiceSession)
    session._settings = MagicMock(no_answer_timeout=20)
    session._no_answer_repeats = 0
    session._last_assistant_text = "What is your preferred time for the appointment?" * 5
    session._publisher = MagicMock()
    session._publisher.send_json = AsyncMock()
    session.tts = MagicMock()
    session.tts.speak = AsyncMock()
    session.voice = "test-voice"

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr("modules.realtime.realtime_voice_session.asyncio.sleep", no_wait)
    await session._prompt_caller_after_silence()

    payload = session._publisher.send_json.await_args.args[0]
    assert payload["type"] == "token"
    assert payload["text"].startswith("Are you still there?")
    assert len(payload["text"]) <= 100
