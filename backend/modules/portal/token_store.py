"""Per-session holder for the portal JWT (SRP).

The portal issues its JWT only as an httpOnly cookie. Previously a single
process-wide slot carried the token, so concurrent users overwrote each
other. The token is now carried in a ``contextvars.ContextVar`` that is set
per session (the WebSocket handler sets it for the live call, and HTTP
routes resolve it from the request cookie instead). Tools and the portal
client read it through the helpers below, so each async task sees only its
own token and location.

``PortalTokenStore`` is retained as a thin compatibility shim that delegates
to the context variables, so existing callers and injected test doubles keep
working.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Optional

_session_token: ContextVar[Optional[str]] = ContextVar("portal_session_token", default=None)
_session_location_id: ContextVar[Optional[str]] = ContextVar("portal_session_location_id", default=None)


def get_portal_token() -> Optional[str]:
    """Return the current session's portal token, or ``None``."""
    return _session_token.get()


def set_portal_token(token: str, location_id: Optional[str] = None) -> None:
    """Remember the token for the current session.

    A different token invalidates any previously cached location; a provided
    ``location_id`` seeds the new session's location.
    """
    if token != _session_token.get():
        _session_location_id.set(None)
    _session_token.set(token)
    if location_id:
        _session_location_id.set(str(location_id))


def clear_portal_token() -> None:
    """Forget the token and location for the current session."""
    _session_token.set(None)
    _session_location_id.set(None)


def get_portal_location_id() -> Optional[str]:
    """Return the current session's cached location id, or ``None``."""
    return _session_location_id.get()


def set_portal_location_id(location_id: Optional[str]) -> None:
    """Cache the current session's active location id (ignored when empty)."""
    if location_id:
        _session_location_id.set(str(location_id))


class PortalTokenStore:
    """Compatibility shim delegating to the per-session context variables."""

    def set(self, token: str, location_id: Optional[str] = None) -> None:
        set_portal_token(token, location_id=location_id)

    def get(self) -> Optional[str]:
        return get_portal_token()

    def set_location(self, location_id: Optional[str]) -> None:
        set_portal_location_id(location_id)

    def location_id(self) -> Optional[str]:
        return get_portal_location_id()

    def clear(self) -> None:
        clear_portal_token()


# Shared instance: delegates to the per-session context variables.
portal_token_store = PortalTokenStore()
