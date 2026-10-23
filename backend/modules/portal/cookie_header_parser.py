"""Cookie-header parsing helpers (SRP).

Used to pull the chatbot-domain cookie back out of an incoming request —
notably the websocket handshake, where the browser ships cookies before
any FastAPI dependency runs.
"""

from __future__ import annotations

from http.cookies import CookieError, SimpleCookie
from typing import Optional


def extract_token_from_cookie_header(header: Optional[str], cookie_name: str) -> Optional[str]:
    """Return the value of ``cookie_name`` in a raw Cookie header, if any."""
    if not header:
        return None
    jar = SimpleCookie()
    try:
        jar.load(header)
    except CookieError:
        return None
    morsel = jar.get(cookie_name)
    return morsel.value if morsel and morsel.value else None
