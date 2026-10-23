"""Auth routes: chatbot login page -> portal JWT -> chatbot-domain cookie.

Flow (per plan): the frontend login page posts portal credentials here;
the backend exchanges them at ``POST /admin/auth/login`` on the
customer-portal, captures the JWT from the ``access_token`` cookie, mirrors
it into a chatbot-domain HttpOnly cookie, and keeps a copy in the token
store for the voice/websocket side. Every later request ships the cookie
and routes forward it as ``Authorization: Bearer``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from modules.common.settings import get_settings
from modules.portal.portal_http_client import CustomerPortalHttpClient, get_shared_portal_http_client
from modules.portal.portal_token_dependency import resolve_portal_token_from_request
from modules.portal.errors import PortalApiError, PortalAuthError, PortalNetworkError
from modules.portal.session import extract_location_id_from_jwt
from modules.portal.token_store import portal_token_store

router = APIRouter(prefix="/api/auth", tags=["Auth"])


class PortalLoginRequest(BaseModel):
    """Credentials collected by the frontend login page."""

    username: str = Field(min_length=1, description="Portal username")
    password: str = Field(min_length=1, description="Portal password")


def _set_portal_cookie(response: Response, token: str) -> None:
    """Mirror the portal JWT into a chatbot-domain HttpOnly cookie."""
    settings = get_settings()
    response.set_cookie(
        key=settings.portal_cookie_name,
        value=token,
        max_age=settings.portal_cookie_max_age,
        httponly=True,
        # Lax keeps localhost cross-port flows working (mirrors the portal
        # dev behavior); flip to strict behind HTTPS in production.
        samesite="lax",
        secure=False,
        path="/",
    )


@router.post("/login")
async def login(
    payload: PortalLoginRequest,
    response: Response,
    client: CustomerPortalHttpClient = Depends(get_shared_portal_http_client),
) -> dict:
    """Sign in to the customer portal and set the chatbot cookie."""
    try:
        token = await client.login(payload.username, payload.password)
    except PortalAuthError as error:
        raise HTTPException(status_code=401, detail=str(error)) from error
    except PortalNetworkError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    except PortalApiError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    portal_token_store.set(token, location_id=extract_location_id_from_jwt(token))
    _set_portal_cookie(response, token)
    return {"message": "Signed in"}


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    client: CustomerPortalHttpClient = Depends(get_shared_portal_http_client),
) -> dict:
    """Clear the local session; best-effort revoke at the portal too."""
    settings = get_settings()
    token = request.cookies.get(settings.portal_cookie_name) or portal_token_store.get()
    if token:
        try:
            await client.request("POST", "/admin/auth/logout", token=token)
        except PortalApiError:
            pass  # already invalid server-side — local clear is enough
    portal_token_store.clear()
    response.delete_cookie(key=settings.portal_cookie_name, path="/")
    return {"message": "Signed out"}


@router.get("/me")
async def me(
    token: str = Depends(resolve_portal_token_from_request),
    client: CustomerPortalHttpClient = Depends(get_shared_portal_http_client),
) -> dict:
    """Validate the current token; returns the portal user/location context."""
    try:
        envelope = await client.get_token_details(token)
    except PortalAuthError as error:
        # Expired or revoked at the portal — drop our copy so the next
        # request (and the frontend's /me probe) goes to /login.
        portal_token_store.clear()
        raise HTTPException(status_code=401, detail=str(error)) from error
    except PortalApiError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    # Cache the active location while we have the session context.
    data = envelope.get("data") if isinstance(envelope, dict) else None
    portal_token_store.set_location((data or {}).get("locationId"))
    return envelope
