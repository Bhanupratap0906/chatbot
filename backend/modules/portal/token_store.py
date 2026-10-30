"""In-process holder for the portal JWT session (SRP).

The portal issues its JWT only as an httpOnly cookie; the backend captures
it at login and keeps it here so non-HTTP callers (websocket voice session,
LangChain tools) can authenticate without threading the token through every
layer. HTTP routes prefer the browser cookie and fall back to this store.

The store also caches the session's active ``locationId`` (decoded from the
JWT claim by ``modules/portal/session.py``) so service/slot lookups target
the same location ``appointmentBook`` uses server-side.
"""

from __future__ import annotations

from typing import Optional


class PortalTokenStore:
    """Single-slot store for the most recent portal login token + location."""

    def __init__(self) -> None:
        """Start empty; the first login (or WS cookie) fills it."""
        self._token: Optional[str] = None
        self._location_id: Optional[str] = None

    def set(self, token: str, location_id: Optional[str] = None) -> None:
        """Remember the token issued by the portal login.

        A different token invalidates any previously cached location; a
        provided ``location_id`` seeds the new session's location.
        """
        if token != self._token:
            self._location_id = None
        self._token = token
        if location_id:
            self._location_id = str(location_id)

    def get(self) -> Optional[str]:
        """Return the current token, or ``None`` when nobody is signed in."""
        return self._token

    def set_location(self, location_id: Optional[str]) -> None:
        """Cache the session's active location id (ignored when empty)."""
        if location_id:
            self._location_id = str(location_id)

    def location_id(self) -> Optional[str]:
        """Return the cached active location id, or ``None`` if unresolved."""
        return self._location_id

    def clear(self) -> None:
        """Forget the token and location (logout or invalidation)."""
        self._token = None
        self._location_id = None


# Shared instance: one signed-in portal session per backend process.
portal_token_store = PortalTokenStore()
