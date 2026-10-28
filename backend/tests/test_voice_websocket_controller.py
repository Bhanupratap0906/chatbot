"""Item 7/21: websocket pre-accept checks and greeting-task supervision."""

from __future__ import annotations

import asyncio
import os
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.common.settings import get_settings
from modules.realtime import voice_websocket_controller as vwc
from modules.realtime.voice_websocket_controller import (
    _run_voice_session_pumps,
    handle_voice_websocket_connection,
)


class FakeWebSocket:
    def __init__(self, headers=None, client_host="127.0.0.1"):
        self.headers = headers or {}
        self.client = type("C", (), {"host": client_host})()
        self.accepted = False
        self.closed = None
        self.sent = []

    async def accept(self):
        self.accepted = True

    async def close(self, code=1000, reason=""):
        self.closed = (code, reason)

    async def send_json(self, payload):
        self.sent.append(payload)


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    vwc._active_connections.clear()
    # reset lock
    vwc._connections_lock = None
    # clear settings cache after each test
    yield
    get_settings.cache_clear()


def _clear_settings_cache():
    get_settings.cache_clear()


async def test_missing_cookie_closes_1008():
    os.environ["ALLOWED_ORIGINS"] = "*"
    _clear_settings_cache()
    ws = FakeWebSocket(headers={"cookie": ""})
    await handle_voice_websocket_connection(ws)
    assert ws.closed is not None
    assert ws.closed[0] == 1008
    assert not ws.accepted


async def test_bad_origin_closes_1008():
    os.environ["ALLOWED_ORIGINS"] = "http://localhost:3000"
    _clear_settings_cache()
    ws = FakeWebSocket(
        headers={"origin": "http://evil.com", "cookie": "portal_access_token=aaa.bbb.ccc"}
    )
    await handle_voice_websocket_connection(ws)
    assert ws.closed is not None
    assert ws.closed[0] == 1008
    assert not ws.accepted


async def test_call_duration_limit_returns():
    os.environ["MAX_CALL_SECONDS"] = "0"
    os.environ["ALLOWED_ORIGINS"] = "*"
    _clear_settings_cache()
    settings = get_settings()

    class FakeSession:
        def __init__(self):
            self._settings = settings
            self.turn = None
            self.costs = type("C", (), {"print_summary": lambda self: None})()
            self.session_id = "sid"

        async def pump_browser_audio_to_deepgram(self):
            await asyncio.sleep(100)

        async def pump_deepgram_events_to_turns(self):
            await asyncio.sleep(100)

        async def keep_deepgram_connection_alive(self):
            await asyncio.sleep(100)

        async def send_configured_greeting(self):
            await asyncio.sleep(100)

    session = FakeSession()
    dg = MagicMock()
    dg.send = AsyncMock()
    dg.close = AsyncMock()
    ws = FakeWebSocket(headers={"cookie": "portal_access_token=aaa.bbb.ccc"})

    start = time.monotonic()
    await _run_voice_session_pumps(ws, dg, session)
    elapsed = time.monotonic() - start
    assert elapsed < 1.0
