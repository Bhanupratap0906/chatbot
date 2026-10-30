"""Regression tests for voice turn, confirmation, and greeting interruption."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from modules.common.booking_state import BookingState
from modules.realtime.realtime_voice_session import RealtimeVoiceSession


class _Buffer:
    def __init__(self, text: str):
        self.text = text

    def take_pending_transcript(self) -> str:
        text, self.text = self.text, ""
        return text


async def test_confirmation_backchannel_reaches_workflow_while_turn_active() -> None:
    session = RealtimeVoiceSession.__new__(RealtimeVoiceSession)
    active_task = asyncio.create_task(asyncio.sleep(60))
    session.turns = SimpleNamespace(
        task=active_task,
        start_assistant_turn=AsyncMock(),
    )
    session.workflow = SimpleNamespace(
        booking_state=BookingState(readback_done=True, user_confirmed=False)
    )
    session._buffer = _Buffer("Yes")
    session._cancel_prompt_caller_after_silence = MagicMock()
    session.cancel_configured_greeting = AsyncMock(return_value=False)

    try:
        await session.start_assistant_turn_from_buffer()
        session.turns.start_assistant_turn.assert_awaited_once_with("Yes")
    finally:
        active_task.cancel()
        await asyncio.gather(active_task, return_exceptions=True)


async def test_passive_backchannel_is_ignored_while_turn_active() -> None:
    session = RealtimeVoiceSession.__new__(RealtimeVoiceSession)
    active_task = asyncio.create_task(asyncio.sleep(60))
    session.turns = SimpleNamespace(
        task=active_task,
        start_assistant_turn=AsyncMock(),
    )
    session.workflow = SimpleNamespace(booking_state=BookingState())
    session._buffer = _Buffer("Okay")
    session._cancel_prompt_caller_after_silence = MagicMock()
    session.cancel_configured_greeting = AsyncMock(return_value=False)

    try:
        await session.start_assistant_turn_from_buffer()
        session.turns.start_assistant_turn.assert_not_awaited()
    finally:
        active_task.cancel()
        await asyncio.gather(active_task, return_exceptions=True)


async def test_meaningful_short_reply_is_not_discarded() -> None:
    session = RealtimeVoiceSession.__new__(RealtimeVoiceSession)
    active_task = asyncio.create_task(asyncio.sleep(60))
    session.turns = SimpleNamespace(
        task=active_task,
        start_assistant_turn=AsyncMock(),
    )
    session.workflow = SimpleNamespace(booking_state=BookingState())
    session._buffer = _Buffer("No")
    session._cancel_prompt_caller_after_silence = MagicMock()
    session.cancel_configured_greeting = AsyncMock(return_value=False)

    try:
        await session.start_assistant_turn_from_buffer()
        session.turns.start_assistant_turn.assert_awaited_once_with("No")
    finally:
        active_task.cancel()
        await asyncio.gather(active_task, return_exceptions=True)


async def test_active_greeting_is_cancelled_and_client_interrupted() -> None:
    session = RealtimeVoiceSession.__new__(RealtimeVoiceSession)
    session._publisher = MagicMock()
    session._publisher.send_json = AsyncMock()
    session._greeting_task = asyncio.create_task(asyncio.sleep(60))

    cancelled = await session.cancel_configured_greeting()

    assert cancelled is True
    assert session._greeting_task is None
    session._publisher.send_json.assert_awaited_once_with({"type": "interrupt"})


async def test_speech_started_interrupts_greeting_even_without_barge_in() -> None:
    session = RealtimeVoiceSession.__new__(RealtimeVoiceSession)
    session._settings = SimpleNamespace(barge_in=False)
    session.cancel_configured_greeting = AsyncMock(return_value=True)

    await session._process_deepgram_event('{"type": "SpeechStarted"}')

    session.cancel_configured_greeting.assert_awaited_once_with()
