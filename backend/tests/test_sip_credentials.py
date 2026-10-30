"""Fix 1: SIP credentials endpoint validates the token against the portal."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from modules.portal.errors import PortalAuthError, PortalApiError
from modules.sip.sip_credentials_router import sip_credentials


async def test_fake_token_returns_401() -> None:
    client = AsyncMock()
    client.get_token_details.side_effect = PortalAuthError("bad")
    with pytest.raises(HTTPException) as exc:
        await sip_credentials(token="totally-fake", client=client)
    assert exc.value.status_code == 401


async def test_portal_error_returns_502() -> None:
    client = AsyncMock()
    client.get_token_details.side_effect = PortalApiError("boom", status_code=500)
    with pytest.raises(HTTPException) as exc:
        await sip_credentials(token="aaa.bbb.ccc", client=client)
    assert exc.value.status_code == 502


async def test_valid_token_returns_200() -> None:
    client = AsyncMock()
    client.get_token_details.return_value = {"statusCode": 200}
    result = await sip_credentials(token="aaa.bbb.ccc", client=client)
    assert "password" in result
    client.get_token_details.assert_awaited_once()
