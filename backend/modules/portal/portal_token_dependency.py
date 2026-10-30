"""FastAPI dependency that resolves the portal token for a request (SRP).

Source of truth is the chatbot-domain cookie set at login (design: token
lives in the browser); the in-process store is the fallback for callers
that never round-tripped a cookie (curl, restarts, websocket sessions).
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from modules.common.settings import get_settings


def resolve_portal_token_from_request(request: Request) -> str:
    """Return the portal JWT for this request or raise ``401``."""
    settings = get_settings()
    token = request.cookies.get(settings.portal_cookie_name)
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Not signed in to the customer portal — sign in first.",
        )
    return token
