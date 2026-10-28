"""Auth routes: chatbot login page -> portal JWT -> chatbot-domain cookie.

Flow (per plan): the frontend login page posts portal credentials here;
the backend exchanges them at ``POST /admin/auth/login`` on the
customer-portal, captures the JWT from the ``access_token`` cookie, mirrors
it into a chatbot-domain HttpOnly cookie, and keeps a copy in the token
store for the voice/websocket side. Every later request ships the cookie
and routes forward it as ``Authorization: Bearer``.
"""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from modules.common.settings import get_settings
from modules.portal.portal_http_client import CustomerPortalHttpClient, get_shared_portal_http_client
from modules.portal.portal_token_dependency import resolve_portal_token_from_request
from modules.portal.errors import PortalApiError, PortalAuthError, PortalNetworkError



# ---------- login rate limiting (5 attempts/min per IP) ----------

_LOGIN_ATTEMPTS: dict[str, list[float]] = {}
_LOGIN_LOCK = asyncio.Lock()


async def _check_login_rate_limit(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    async with _LOGIN_LOCK:
        recent = [t for t in _LOGIN_ATTEMPTS.get(ip, []) if now - t < 60.0]
        if len(recent) >= 5:
            raise HTTPException(status_code=429, detail="Too many login attempts. Try again later.")
        recent.append(now)
        _LOGIN_ATTEMPTS[ip] = recent

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
        samesite=settings.cookie_samesite,
        secure=settings.cookie_secure,
        path="/",
    )


@router.post("/login", dependencies=[Depends(_check_login_rate_limit)])
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
    token = request.cookies.get(settings.portal_cookie_name)
    if token:
        try:
            await client.request("POST", "/admin/auth/logout", token=token)
        except PortalApiError:
            pass  # already invalid server-side — local clear is enough
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
        # Expired or revoked at the portal.
        raise HTTPException(status_code=401, detail=str(error)) from error
    except PortalApiError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    return envelope
