"""Customer-portal (Daily) integration surface.

``CustomerPortalHttpClient`` talks to the portal, ``auth_router``/``appointments_router``
expose it over FastAPI, and ``portal_token_store`` carries the JWT for the
websocket/tool side where no cookie dependency runs.
"""

from modules.portal.portal_http_client import CustomerPortalHttpClient, get_shared_portal_http_client
from modules.portal.errors import PortalApiError, PortalAuthError, PortalNetworkError
from modules.portal.token_store import portal_token_store

__all__ = [
    "CustomerPortalHttpClient",
    "get_shared_portal_http_client",
    "PortalApiError",
    "PortalAuthError",
    "PortalNetworkError",
    "portal_token_store",
]
