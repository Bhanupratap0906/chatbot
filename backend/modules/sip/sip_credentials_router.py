"""Authenticated SIP credential endpoint (SRP).

Returns the SIP gateway credentials from server-side environment variables
only for a signed-in portal session, so they are never bundled into the
browser bundle.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from modules.common.settings import get_settings
from modules.portal.errors import PortalApiError, PortalAuthError
from modules.portal.portal_http_client import get_shared_portal_http_client
from modules.portal.portal_token_dependency import resolve_portal_token_from_request

router = APIRouter(prefix="/api/sip", tags=["SIP"])


@router.get("/credentials")
async def sip_credentials(
    token: str = Depends(resolve_portal_token_from_request),
    client=Depends(get_shared_portal_http_client),
) -> dict:
    """Return SIP gateway credentials for a signed-in session."""
    try:
        await client.get_token_details(token)
    except PortalAuthError as error:
        raise HTTPException(status_code=401, detail=str(error)) from error
    except PortalApiError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    settings = get_settings()
    return {
        "wsUrl": settings.sip_ws_url,
        "domain": settings.sip_domain,
        "username": settings.sip_username,
        "password": settings.sip_password,
    }
