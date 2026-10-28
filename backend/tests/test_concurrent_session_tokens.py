"""Item 4: per-session token isolation.

Two concurrent sessions with different portal tokens must never observe each
other's token or location. State lives in a ``ContextVar`` set per session.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from modules.portal.token_store import (
    get_portal_location_id,
    get_portal_token,
    set_portal_token,
)
from modules.realtime.realtime_voice_session import RealtimeVoiceSession


async def test_contextvar_tokens_do_not_leak_between_tasks() -> None:
    results: dict[str, str | None] = {}

    async def worker(name: str, token: str, location: str) -> None:
        set_portal_token(token, location_id=location)
        await asyncio.sleep(0)  # yield to the other task
        results[name] = get_portal_token()
        assert get_portal_location_id() == location

    await asyncio.gather(
        worker("a", "token-A", "loc-A"),
        worker("b", "token-B", "loc-B"),
    )

    assert results["a"] == "token-A"
    assert results["b"] == "token-B"


async def test_two_concurrent_sessions_keep_distinct_tokens() -> None:
    captured: dict[str, str | None] = {}

    async def run_session(name: str, token: str, location: str) -> None:
        session = RealtimeVoiceSession(
            MagicMock(),
            MagicMock(),
            portal_token=token,
            portal_location_id=location,
        )
        # Simulate the session doing work in its own task context.
        await asyncio.sleep(0)
        captured[name] = get_portal_token()
        assert session.portal_token == token

    await asyncio.gather(
        run_session("a", "token-A", "loc-A"),
        run_session("b", "token-B", "loc-B"),
    )

    assert captured["a"] == "token-A"
    assert captured["b"] == "token-B"
