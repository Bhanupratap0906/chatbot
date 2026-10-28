"""Realtime websocket route — thin adapter (SRP + DIP).

Only HTTP/websocket concerns live here: accept, config check, Deepgram
connect, ``ready`` reply, task supervision, cleanup. All conversation
logic lives in ``RealtimeVoiceSession`` / ``AssistantTurnManager`` / ``GreetingService``,
all built with injected abstractions (DIP).

``RealtimeVoiceSession`` is imported here for construction and exposed for callers
that need to customize a session.
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from modules.common.settings import get_settings
from modules.portal.cookie_header_parser import extract_token_from_cookie_header
from modules.portal.session import extract_location_id_from_jwt
from modules.realtime.realtime_voice_session import RealtimeVoiceSession
from modules.stt.deepgram_realtime_connector import DeepgramRealtimeConnector
from modules.workflow.conversation_workflow_graph import release_thread
from modules.tts.voice_catalog import SAMPLE_RATE as TTS_SAMPLE_RATE
from modules.tts.voice_catalog import VOICES

logger = logging.getLogger("voice")


router = APIRouter(tags=["Realtime"])


async def _run_voice_session_pumps(ws: WebSocket, dg, session: RealtimeVoiceSession) -> None:
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
        asyncio.create_task(session.pump_browser_audio_to_deepgram()),
        asyncio.create_task(session.pump_deepgram_events_to_turns()),
        asyncio.create_task(session.keep_deepgram_connection_alive()),
    ]
    # Runs once and finishes on its own — must not be in the FIRST_COMPLETED
    # race below, or the session tears down the instant the greeting ends.
    asyncio.create_task(session.send_configured_greeting())
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            exc = task.exception()
            if exc and not isinstance(exc, WebSocketDisconnect):
                logger.warning("session ended: %r", exc)
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
        try:
            release_thread(session.session_id)
        except Exception:
            pass
        session.costs.print_summary()
        logger.info("/ws/voice connection closed")


@router.websocket("/ws/voice")
async def handle_voice_websocket_connection(ws: WebSocket):
    """Handle a frontend live-call connection; FastAPI invokes this for ``/ws/voice``."""
    await ws.accept()
    settings = get_settings()

    # The browser ships cookies on the WS handshake: pull the portal token
    # from the chatbot cookie so booking tools can authenticate even when
    # this process never saw the login HTTP request (e.g. after restart).
    ws_token = extract_token_from_cookie_header(
        ws.headers.get("cookie"), settings.portal_cookie_name
    )
    if not settings.deepgram_api_key:
        await ws.close(code=1011, reason="DEEPGRAM_API_KEY is not configured")
        return

    try:
        dg = await DeepgramRealtimeConnector(
            api_key=settings.deepgram_api_key,
            input_sample_rate=settings.input_sample_rate,
        ).connect_to_deepgram_realtime()
    except Exception as error:
        logger.error("failed to connect to Deepgram: %r", error)
        await ws.close(code=1011, reason="Failed to connect to speech-to-text service")
        return

    # DIP: session builds its default LLM/TTS internally, but they are
    # injectable — see ``RealtimeVoiceSession(ws, dg, llm=..., tts=...)``.
    session = RealtimeVoiceSession(
        ws,
        dg,
        voice=settings.default_voice,
        settings=settings,
        portal_token=ws_token,
        portal_location_id=extract_location_id_from_jwt(ws_token) if ws_token else None,
    )
    await _run_voice_session_pumps(ws, dg, session)


__all__ = ["router", "handle_voice_websocket_connection", "RealtimeVoiceSession"]
