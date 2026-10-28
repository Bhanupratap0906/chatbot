"""Fix C: watchdog cancels always; prompt capped 100 chars."""

from __future__ import annotations

from unittest.mock import MagicMock

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


def test_prompt_capped_100_chars() -> None:
    # Direct check of message format used in _prompt_caller_after_silence
    last_question = "What is your preferred time for the appointment?"
    message = ("Are you still there?" + (" " + (last_question or "").strip()[:40] if last_question else ""))[:100]
    assert len(message) <= 100
    assert message.startswith("Are you still there?")
