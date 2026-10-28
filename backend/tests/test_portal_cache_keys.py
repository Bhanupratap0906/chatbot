"""Item 5: master-data cache must not be shared across users.

The old key used ``token[:12]`` (the JWT header, identical for everyone).
The cache is now keyed on location_id + sha256(full token) inside a bounded
TTLCache, so two different tokens never reuse each other's cached data.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from modules.portal.portal_http_client import CustomerPortalHttpClient


async def test_two_tokens_do_not_share_cached_master_data() -> None:
    client = CustomerPortalHttpClient()
    client.request = AsyncMock(side_effect=lambda *a, **kw: {"token": kw.get("token")})

    first = await client.get_appointment_master_data("token-alpha")
    second = await client.get_appointment_master_data("token-beta")

    assert first == {"token": "token-alpha"}
    assert second == {"token": "token-beta"}
    # Both tokens hit the portal; no cross-user cache reuse.
    assert client.request.await_count == 2


async def test_same_token_reuses_cache() -> None:
    client = CustomerPortalHttpClient()
    client.request = AsyncMock(return_value={"ok": True})

    await client.get_appointment_master_data("token-same")
    await client.get_appointment_master_data("token-same")

    assert client.request.await_count == 1


async def test_procedure_list_cache_keys_by_token() -> None:
    client = CustomerPortalHttpClient()
    client.request = AsyncMock(side_effect=lambda *a, **kw: {"token": kw.get("token")})

    await client.get_procedure_master_list("token-one")
    await client.get_procedure_master_list("token-two")

    assert client.request.await_count == 2
