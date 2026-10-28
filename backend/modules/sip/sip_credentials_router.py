"""Authenticated SIP credential endpoint (SRP).

Returns the SIP gateway credentials from server-side environment variables
only for a signed-in portal session, so they are never bundled into the
browser bundle.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from modules.common.settings import get_settings
from modules.portal.portal_token_dependency import resolve_portal_token_from_request

router = APIRouter(prefix="/api/sip", tags=["SIP"])


@router.get("/credentials")
async def sip_credentials(
    _token: str = Depends(resolve_portal_token_from_request),
) -> dict:
    """Return SIP gateway credentials for a signed-in session."""
    settings = get_settings()
    return {
        "wsUrl": settings.sip_ws_url,
        "domain": settings.sip_domain,
        "username": settings.sip_username,
        "password": settings.sip_password,
    }
