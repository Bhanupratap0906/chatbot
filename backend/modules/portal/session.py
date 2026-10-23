"""Session context derived from the portal JWT (SRP).

The active ``locationId`` lives in the token — the portal's own AuthGuard
reads ``payload.locationId`` (falling back to the user's first allowed
location) and every location-scoped endpoint (services, slots,
``appointmentBook``'s user context) keys off it. This module resolves the
same value for the chatbot:

1. cached in ``portal_token_store`` (populated at login/handshake),
2. decoded from the JWT payload claim (zero network cost),
3. fetched from ``GET /admin/auth/get-token-details`` when the claim is
   absent (mirrors the server-side fallback),
4. ``PORTAL_LOCATION_ID`` as a last-resort override for tokenless contexts.

The JWT payload is only *decoded*, never signature-verified — the portal
validates the signature on every guarded call anyway; we just read claims
from a token it issued to us.
"""

from __future__ import annotations

import base64
import json
from typing import Optional

from modules.common.settings import get_settings
from modules.portal.portal_http_client import get_shared_portal_http_client
from modules.portal.errors import PortalApiError, PortalAuthError
from modules.portal.token_store import PortalTokenStore, portal_token_store


def extract_location_id_from_jwt(token: str) -> Optional[str]:
    """Decode the ``locationId`` claim from a portal JWT payload, if present."""
    try:
        payload_segment = token.split(".")[1]
        padded = payload_segment + "=" * (-len(payload_segment) % 4)
        claims = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    except (IndexError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    location_id = claims.get("locationId")
    return location_id if isinstance(location_id, str) and location_id else None


async def resolve_active_location_id(
    token: str,
    *,
    client=None,
    store: Optional[PortalTokenStore] = None,
) -> str:
    """Return the active location id for ``token`` (possibly ``""``).

    Resolution order: store cache -> JWT claim -> token-details -> env
    override. Raises ``PortalAuthError`` unchanged so callers keep their
    expired-session handling.
    """
    active_store = store or portal_token_store

    cached = active_store.location_id()
    if cached:
        return cached

    claim = extract_location_id_from_jwt(token)
    if claim:
        active_store.set_location(claim)
        return claim

    # Claim absent: ask the portal (same fallback the AuthGuard applies).
    try:
        envelope = await (client or get_shared_portal_http_client()).get_token_details(token)
    except PortalAuthError:
        active_store.clear()
        raise
    except PortalApiError:
        envelope = {}
    data = envelope.get("data") if isinstance(envelope, dict) else None
    location_id = str((data or {}).get("locationId") or "")
    if location_id:
        active_store.set_location(location_id)
        return location_id

    # Last resort: explicit override for contexts without a resolvable session.
    return get_settings().portal_location_id


def encrypt_location_id_for_portal(location_id: str) -> str:
    """Encrypt a location id the same way the portal's ``encrypt`` helper does.

    The ``/appointments/getAppointmentSlots`` endpoint decrypts the incoming
    ``locationId`` with AES-256-CBC (key = sha256('my-super-secret-key')),
    so callers must send the encrypted ``iv:hex`` form.
    """
    import hashlib
    import os

    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    key = hashlib.sha256(b"my-super-secret-key").digest()
    iv = os.urandom(16)
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    encryptor = cipher.encryptor()

    data = location_id.encode("utf-8")
    padding = 16 - (len(data) % 16)
    padded = data + bytes([padding]) * padding
    encrypted = encryptor.update(padded) + encryptor.finalize()
    return iv.hex() + ":" + encrypted.hex()
