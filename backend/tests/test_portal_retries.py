"""Item 20 part 1: portal client retries on transient network errors."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from modules.portal.errors import PortalNetworkError
from modules.portal.portal_http_client import CustomerPortalHttpClient


async def test_retries_on_network_error_then_succeeds() -> None:
    client = CustomerPortalHttpClient()
    # Fail twice, succeed third.
    client._http.request = AsyncMock(side_effect=[
        httpx.ConnectError("boom"),
        httpx.ConnectError("boom"),
        MagicMock(status_code=200, json=lambda: {"statusCode": 200, "data": {"ok": True}}),
    ])
    # _try_json reads response; make it dict-like
    # Patch _try_json
    import modules.portal.portal_http_client as m
    m._try_json = lambda r: {"statusCode": 200, "data": {"ok": True}}
    result = await client.request("GET", "/x")
    assert result == {"statusCode": 200, "data": {"ok": True}}
    assert client._http.request.await_count == 3


async def test_circuit_breaker_opens_after_failures() -> None:
    client = CustomerPortalHttpClient()
    client._cb_threshold = 2
    client._http.request = AsyncMock(side_effect=httpx.ConnectError("boom"))
    import modules.portal.portal_http_client as m
    m._try_json = lambda r: {}
    for _ in range(2):
        try:
            await client.request("GET", "/x")
        except PortalNetworkError:
            pass
    # circuit open
    with pytest.raises(PortalNetworkError):
        await client.request("GET", "/x")
