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
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from modules.common.call_context import set_call_id
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


# ---------- per-IP concurrent connection limit ----------

_active_connections: dict[str, int] = {}
_connections_lock: Optional[asyncio.Lock] = None


def _get_connections_lock() -> asyncio.Lock:
    global _connections_lock
    if _connections_lock is None:
        _connections_lock = asyncio.Lock()
    return _connections_lock


async def _acquire_connection_slot(ip: str, limit: int) -> bool:
    async with _get_connections_lock():
        if _active_connections.get(ip, 0) >= limit:
            return False
        _active_connections[ip] = _active_connections.get(ip, 0) + 1
        return True


async def _release_connection_slot(ip: str) -> None:
    async with _get_connections_lock():
        if ip in _active_connections:
            _active_connections[ip] -= 1
            if _active_connections[ip] <= 0:
                del _active_connections[ip]


def _log_task_exception(task: asyncio.Task) -> None:
    try:
        exc = task.exception()
    except (asyncio.CancelledError, asyncio.InvalidStateError):
        return
    if exc is not None:
        logger.warning("send_configured_greeting failed: %r", exc)


def _origin_allowed(origin: Optional[str], allowed: list[str]) -> bool:
    if "*" in allowed:
        return True
    if origin is None:
        return True
    return origin in allowed


def _token_looks_valid(token: Optional[str]) -> bool:
    if not token:
        return False
    return len(token.split(".")) == 3


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
    greeting_task = asyncio.create_task(session.send_configured_greeting())
    greeting_task.add_done_callback(_log_task_exception)
    settings = session._settings
    try:
        done, pending = await asyncio.wait(
            tasks, return_when=asyncio.FIRST_COMPLETED, timeout=settings.max_call_seconds
        )
        for task in pending:
            task.cancel()
        for task in done:
            exc = task.exception()
            if exc and not isinstance(exc, WebSocketDisconnect):
                logger.warning("session ended: %r", exc)
    finally:
        greeting_task.cancel()
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
    settings = get_settings()
    set_call_id()

    # --- pre-accept checks (close with 1008 before accept) ---
    origin = ws.headers.get("origin")
    if not _origin_allowed(origin, settings.allowed_origins):
        await ws.close(code=1008, reason="Origin not allowed")
        return

    ws_token = extract_token_from_cookie_header(
        ws.headers.get("cookie"), settings.portal_cookie_name
    )
    if not _token_looks_valid(ws_token):
        await ws.close(code=1008, reason="Missing or invalid portal cookie")
        return

    client_ip = ws.client.host if ws.client else "unknown"
    slot_acquired = await _acquire_connection_slot(client_ip, settings.max_concurrent_calls_per_ip)
    if not slot_acquired:
        await ws.close(code=1008, reason="Too many concurrent calls from this IP")
        return

    await ws.accept()

    if not settings.deepgram_api_key:
        await ws.close(code=1011, reason="DEEPGRAM_API_KEY is not configured")
        await _release_connection_slot(client_ip)
        return

    try:
        dg = await DeepgramRealtimeConnector(
            api_key=settings.deepgram_api_key,
            input_sample_rate=settings.input_sample_rate,
        ).connect_to_deepgram_realtime()
    except Exception as error:
        logger.error("failed to connect to Deepgram: %r", error)
        await ws.close(code=1011, reason="Failed to connect to speech-to-text service")
        await _release_connection_slot(client_ip)
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
    try:
        await _run_voice_session_pumps(ws, dg, session)
    finally:
        await _release_connection_slot(client_ip)


__all__ = ["router", "handle_voice_websocket_connection", "RealtimeVoiceSession"]
