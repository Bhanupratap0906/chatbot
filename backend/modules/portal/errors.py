"""Portal API error types (SRP).

The customer-portal wraps failures in ``{statusCode, message, errors}``
and the HTTP status mirrors ``statusCode``. These exceptions normalize
that contract so callers only distinguish "auth problem" from "everything
else" without touching raw httpx responses.
"""

from __future__ import annotations

from typing import Optional



def flatten_portal_error_details(value) -> str:
    """Flatten nested list/dict/str errors into a single string."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple, set)):
        return "; ".join(flatten_portal_error_details(v) for v in value)
    if isinstance(value, dict):
        return "; ".join(f"{k}: {flatten_portal_error_details(v)}" for k, v in value.items())
    return str(value)


class PortalApiError(Exception):
    """A failed call to the customer-portal API."""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        errors: str = "",
    ) -> None:
        """Store the human message, HTTP status, and portal error detail."""
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.errors = flatten_portal_error_details(errors)

    def __str__(self) -> str:
        """Render message with status/detail when present."""
        parts = [self.message]
        if self.status_code is not None:
            parts.append(f"(status {self.status_code})")
        if self.errors:
            parts.append(f"— {self.errors}")
        return " ".join(parts)


class PortalAuthError(PortalApiError):
    """401/403: the portal token is missing, expired, or revoked."""


class PortalNetworkError(PortalApiError):
    """The portal could not be reached at all (connect/timeout errors)."""
