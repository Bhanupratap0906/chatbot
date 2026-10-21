"""Realtime websocket route — thin adapter (SRP + DIP).

Only HTTP/websocket concerns live here: accept, config check, Deepgram
connect, ``ready`` reply, task supervision, cleanup. All conversation
logic lives in ``VoiceSession`` / ``TurnManager`` / ``GreetingService``,
all built with injected abstractions (DIP).

``VoiceSession`` is imported here for construction and exposed for callers
that need to customize a session.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from modules.common.settings import get_settings
from modules.realtime.session import VoiceSession
from modules.stt.connector import DeepgramRealtimeConnector
from modules.tts.voices import SAMPLE_RATE as TTS_SAMPLE_RATE
from modules.tts.voices import VOICES


router = APIRouter(tags=["Realtime"])


async def _run_session(ws: WebSocket, dg, session: VoiceSession) -> None:
    """Run session pumps and guarantee cleanup; called after websocket setup succeeds."""
    await ws.send_json(
        {
            "type": "ready",
            "inputSampleRate": session._settings.input_sample_rate,
            "ttsSampleRate": TTS_SAMPLE_RATE,
            "voices": VOICES,
        }
    )
    tasks = [
        asyncio.create_task(session.pump_client()),
        asyncio.create_task(session.pump_deepgram()),
        asyncio.create_task(session.keepalive()),
    ]
    # Runs once and finishes on its own — must not be in the FIRST_COMPLETED
    # race below, or the session tears down the instant the greeting ends.
    asyncio.create_task(session.send_greeting())
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            exc = task.exception()
            if exc and not isinstance(exc, WebSocketDisconnect):
                print("Session ended:", repr(exc))
    finally:
        turn = session.turn
        if turn and not turn.done():
            turn.cancel()
        try:
            await dg.send(json.dumps({"type": "CloseStream"}))
        except Exception:
            pass
        await dg.close()
        try:
            await ws.close()
        except Exception:
            pass
        session.costs.print_summary()
        print("[ws] /ws/voice connection closed")


@router.websocket("/ws/voice")
async def voice_socket(ws: WebSocket):
    """Handle a frontend live-call connection; FastAPI invokes this for ``/ws/voice``."""
    await ws.accept()
    settings = get_settings()

    if not settings.deepgram_api_key:
        await ws.close(code=1011, reason="DEEPGRAM_API_KEY is not configured")
        return

    try:
        dg = await DeepgramRealtimeConnector(
            api_key=settings.deepgram_api_key,
            input_sample_rate=settings.input_sample_rate,
        ).connect()
    except Exception as error:
        print("Failed to connect to Deepgram:", repr(error))
        await ws.close(code=1011, reason="Failed to connect to speech-to-text service")
        return

    # DIP: session builds its default LLM/TTS internally, but they are
    # injectable — see ``VoiceSession(ws, dg, llm=..., tts=...)``.
    session = VoiceSession(ws, dg, voice=settings.default_voice, settings=settings)
    await _run_session(ws, dg, session)


__all__ = ["router", "voice_socket", "VoiceSession"]
