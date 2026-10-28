"""HTTP client for the customer-portal (Daily) REST API (SRP + DIP).

One ``CustomerPortalHttpClient`` wraps httpx with the portal's two contracts:

* **Auth** — login returns the JWT *only* as an ``access_token`` cookie;
  every guarded call replays it as ``Authorization: Bearer <jwt>``.
* **Envelope** — bodies are ``{statusCode, message, data, errors?}`` and
  the HTTP status mirrors ``statusCode``; failures are normalized into
  :class:`PortalApiError` / :class:`PortalAuthError`.

Every portal request emits exactly one structured log line via
``_ApiCallLog`` so success and failure are uniformly auditable:

    [api] POST /admin/auth/login -> 200 (142.3ms) ok
    [api] POST /patient/by-mobile -> 200 (88.1ms) ok
    [api] POST /appointments/appointmentBook -> 409 (120.2ms) error=...

Full request/response bodies are logged by default; ``API_LOG_BODIES=false``
switches to payload summaries. Passwords
and bearer tokens are always redacted. Callers (routers, LangChain tools)
depend only on this class — the base URL and timeout come from ``Settings``.
"""

from __future__ import annotations

import json as jsonlib
import logging
import time
from contextvars import ContextVar
from typing import Any, Optional

import httpx

from modules.common.settings import get_settings
from modules.portal.dtos import (
    AppointmentBookRequestDTO,
    AppointmentsListRequestDTO,
    GetAppointmentDetailsByIdRequestDTO,
    GetAppointmentSlotsRequestDto,
    GetPatientDetailsByMobileNumberRequestDto,
    GetServiceByLocationIdRequestDto,
)
from modules.portal.errors import PortalApiError, PortalAuthError, PortalNetworkError, flatten_portal_error_details

# The portal's auth cookie name (AUTH_COOKIE_NAME in customer-portal).
PORTAL_AUTH_COOKIE = "access_token"

logger = logging.getLogger("portal.api")
_API_LOG_BODIES = get_settings().api_log_bodies

# Per-call audit trail (populated by RealtimeVoiceSession for end-of-call summary).
_API_AUDIT: ContextVar = ContextVar("api_audit", default=None)


class _ApiCallLog:
    """Collects one request's metadata and emits a single structured log line."""

    def __init__(self, method: str, url: str) -> None:
        self._method = method.upper()
        self._build_portal_url = url
        self._started = time.perf_counter()
        self._request: Any = None

    def request(self, json: Optional[dict] = None, params: Optional[dict] = None) -> None:
        """Capture the outgoing payload for the log line (redacted)."""
        self._request = {
            "json": _render_request(json),
            "params": _render_request(params),
        }

    def _elapsed_ms(self) -> float:
        return (time.perf_counter() - self._started) * 1000

    def ok(self, status: int, body: Any) -> None:
        logger.info(
            "[api] %s %s -> %s (%.1fms) ok request=%s response=%s",
            self._method, self._build_portal_url, status, self._elapsed_ms(),
            self._request, _render_response(body),
        )

    def fail(self, status: int, body: Any, error: str) -> None:
        logger.warning(
            "[api] %s %s -> %s (%.1fms) error=%s request=%s response=%s",
            self._method, self._build_portal_url, status, self._elapsed_ms(), error,
            self._request, _render_response(body),
        )

    def network_error(self, error: Exception) -> None:
        logger.warning(
            "[api] %s %s -> unreachable (%.1fms) error=%s request=%s",
            self._method, self._build_portal_url, self._elapsed_ms(), error, self._request,
        )


class CustomerPortalHttpClient:
    """Thin async wrapper over the customer-portal REST API."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: Optional[float] = None,
        http: Optional[httpx.AsyncClient] = None,
    ) -> None:
        """Build the client from ``Settings`` unless explicit values are given."""
        settings = get_settings()
        self._base_url = (base_url or settings.portal_base_url).rstrip("/")
        self._http = http or httpx.AsyncClient(
            timeout=timeout or settings.portal_timeout
        )
        self._cache: dict[str, tuple[float, dict]] = {}
        self._cache_ttl = 300.0

    # ---------- transport ----------

    def _build_portal_url(self, path: str) -> str:
        """Join a portal path (``/appointments/...``) onto the base URL."""
        return f"{self._base_url}/{path.lstrip('/')}"

    @staticmethod
    def _raise_portal_error_for_envelope(status_code: int, body: Any) -> None:
        """Map HTTP + envelope status codes onto typed portal errors."""
        envelope_status = (
            body.get("statusCode") if isinstance(body, dict) else None
        )
        effective = envelope_status if isinstance(envelope_status, int) else status_code
        if effective < 400:
            return
        message = (
            body.get("message") or "Portal request failed"
            if isinstance(body, dict)
            else "Portal request failed"
        )
        errors = flatten_portal_error_details(body.get("errors")) if isinstance(body, dict) else ""
        if effective in (401, 403):
            raise PortalAuthError(message, status_code=effective, errors=errors)
        raise PortalApiError(message, status_code=effective, errors=errors)

    async def request(
        self,
        method: str,
        path: str,
        *,
        token: Optional[str] = None,
        json: Optional[dict] = None,
        params: Optional[dict] = None,
    ) -> dict:
        """Perform one portal call; return the envelope or raise a typed error."""
        headers = {"Authorization": f"Bearer {token}"} if token else None
        log = _ApiCallLog(method, self._build_portal_url(path))
        log.request(json=json, params=params)
        try:
            response = await self._http.request(
                method,
                self._build_portal_url(path),
                json=json,
                params=params,
                headers=headers,
            )
        except httpx.HTTPError as error:
            log.network_error(error)
            raise PortalNetworkError(f"Cannot reach portal: {error}") from error

        body = _try_json(response)
        try:
            self._raise_portal_error_for_envelope(response.status_code, body)
        except PortalApiError as error:
            log.fail(response.status_code, body, str(error))
            raise

        if not isinstance(body, dict):
            log.fail(response.status_code, body, "non-JSON response")
            raise PortalApiError(
                "Portal returned a non-JSON response",
                status_code=response.status_code,
            )

        log.ok(response.status_code, body)
        _append_portal_api_call_to_audit(self._method if False else method, self._build_portal_url(path), response.status_code, json, params, body)
        return body

    async def aclose(self) -> None:
        """Close the underlying connection pool."""
        await self._http.aclose()

    # ---------- auth ----------

    async def login(self, username: str, password: str) -> str:
        """Exchange credentials for the portal JWT (delivered as a cookie)."""
        log = _ApiCallLog("POST", self._build_portal_url("/admin/auth/login"))
        log.request(json={"username": username, "password": "[REDACTED]"})
        try:
            response = await self._http.post(
                self._build_portal_url("/admin/auth/login"),
                json={"username": username, "password": password},
            )
        except httpx.HTTPError as error:
            log.network_error(error)
            raise PortalNetworkError(f"Cannot reach portal: {error}") from error

        body = _try_json(response)

        # Invalid credentials come back as 400 with the envelope message.
        if response.status_code >= 400:
            message = (
                body.get("message") if isinstance(body, dict) else None
            ) or "Portal login failed"
            log.fail(response.status_code, body, message)
            raise PortalAuthError(message, status_code=response.status_code)

        token = response.cookies.get(PORTAL_AUTH_COOKIE)
        if not token:
            log.fail(response.status_code, body, "missing auth cookie")
            raise PortalAuthError(
                "Portal login returned no access token cookie",
                status_code=response.status_code,
            )

        log.ok(response.status_code, body)
        _append_portal_api_call_to_audit("POST", self._build_portal_url("/admin/auth/login"), response.status_code, {"username": username, "password": "[REDACTED]"}, None, body)
        return token

    async def get_token_details(self, token: str) -> dict:
        """Validate the token and return the signed-in user/location context."""
        return await self.request("GET", "/admin/auth/get-token-details", token=token)

    # ---------- appointments ----------

    async def list_appointments(self, token: str, payload: dict | AppointmentsListRequestDTO) -> dict:
        """List appointments (validated as ``AppointmentsListRequestDTO``)."""
        if isinstance(payload, AppointmentsListRequestDTO):
            payload = payload.to_payload()
        else:
            payload = AppointmentsListRequestDTO.from_dict(payload).to_payload()
        return await self.request(
            "POST", "/appointments/listAllApppointments", token=token, json=payload
        )

    async def get_appointment_details(
        self, token: str, appointment_id: int, source: str = "DAILY"
    ) -> dict:
        """Fetch one appointment by id (``source``: ``DAILY`` or ``EMR``)."""
        params = GetAppointmentDetailsByIdRequestDTO(source=source).to_payload()
        return await self.request(
            "GET",
            f"/appointments/getAppointmentDetailsById/{appointment_id}",
            token=token,
            params=params,
        )

    async def book_appointment(
        self, token: str, payload: dict | AppointmentBookRequestDTO
    ) -> dict:
        """Book an appointment (validated as ``AppointmentBookRequestDTO``)."""
        if isinstance(payload, AppointmentBookRequestDTO):
            payload = payload.to_payload()
        else:
            payload = AppointmentBookRequestDTO.from_dict(payload).to_payload()
        return await self.request(
            "POST", "/appointments/appointmentBook", token=token, json=payload
        )

    async def get_providers(self, token: str) -> dict:
        """All active providers for the authenticated user's location."""
        return await self.request("GET", "/appointments/getAllProviders", token=token)

    async def get_appointment_master_data(self, token: str) -> dict:
        """Aggregated reasons/providers/operatories for appointment booking."""
        import time as _time

        key = f"master:{token[:12]}"
        cached = self._cache.get(key)
        if cached and (_time.time() - cached[0]) < self._cache_ttl:
            return cached[1]
        result = await self.request("GET", "/appointments/getAppointmentMasterData", token=token)
        self._cache[key] = (_time.time(), result)
        return result

    async def get_procedure_master_list(
        self,
        token: str,
        *,
        page: int = 1,
        per_page: int = 20,
        sort_key: str = "serviceName",
        sort_value: str = "asc",
        is_active: bool | None = True,
        search: str | None = None,
    ) -> dict:
        """Paginated procedure (service) master list with providers/operatories."""
        import time as _time

        key = f"proc:{token[:12]}:{page}:{per_page}:{sort_key}:{sort_value}:{is_active}:{search}"
        cached = self._cache.get(key)
        if cached and (_time.time() - cached[0]) < self._cache_ttl:
            return cached[1]
        params: dict[str, Any] = {
            "page": page,
            "perPage": per_page,
            "sortKey": sort_key,
            "sortValue": sort_value,
        }
        if is_active is not None:
            params["isActive"] = str(is_active).lower()
        if search:
            params["search"] = search
        result = await self.request(
            "GET", "/appointments/getProcedureMasterList", token=token, params=params
        )
        self._cache[key] = (_time.time(), result)
        return result

    async def get_patient_list_data(
        self,
        token: str,
        *,
        page: int = 1,
        limit: int = 20,
        search: str | None = None,
        alphabet: str | None = None,
    ) -> dict:
        """Paginated patient list (search by name or mobile)."""
        params: dict[str, Any] = {"page": page, "limit": limit}
        if search:
            params["search"] = search
        if alphabet:
            params["alphabet"] = alphabet
        return await self.request("GET", "/patient/list-data", token=token, params=params)

    async def create_patient(self, token: str, payload: dict) -> dict:
        """Create a patient record (POST /patient)."""
        return await self.request("POST", "/patient", token=token, json=payload)

    # ---------- lookups (public, no token required) ----------

    async def get_services(
        self, location_id: str, provider_id: Optional[int] = None
    ) -> dict:
        """Services offered at a location (optionally filtered by provider)."""
        params = GetServiceByLocationIdRequestDto(
            locationId=location_id, providerId=provider_id
        ).to_payload()
        return await self.request("GET", "/appointments/getServiceByLocationId", params=params)

    async def get_appointment_slots(self, payload: dict | GetAppointmentSlotsRequestDto) -> dict:
        """Available slots (validated as ``GetAppointmentSlotsRequestDto``)."""
        if isinstance(payload, GetAppointmentSlotsRequestDto):
            payload = payload.to_payload()
        else:
            payload = GetAppointmentSlotsRequestDto.from_dict(payload).to_payload()
        return await self.request("POST", "/appointments/getAppointmentSlots", json=payload)

    # ---------- patients (guarded) ----------

    async def patient_by_mobile(
        self, token: str, mobile_number: str, name: Optional[str] = None
    ) -> dict:
        """Look a patient up by mobile number; returns the portal envelope."""
        payload = GetPatientDetailsByMobileNumberRequestDto(
            mobileNumber=mobile_number, name=name or None
        ).to_payload()
        return await self.request("POST", "/patient/by-mobile", token=token, json=payload)




def _append_portal_api_call_to_audit(method, url, status, req_json, req_params, body):
    trail = _API_AUDIT.get()
    if trail is None:
        return
    try:
        trail.append({
            "method": method,
            "url": url,
            "status": status,
            "request": {"json": req_json, "params": req_params},
            "response": body,
        })
    except Exception:
        pass

# One shared client per process (connection pooling + settings parity).
_client: Optional[CustomerPortalHttpClient] = None


def set_api_audit(trail):
    return _API_AUDIT.set(trail)


def reset_api_audit(token) -> None:
    _API_AUDIT.reset(token)


def get_shared_portal_http_client() -> CustomerPortalHttpClient:
    """Return the process-wide ``CustomerPortalHttpClient`` (created on first use)."""
    global _client
    if _client is None:
        _client = CustomerPortalHttpClient()
    return _client


# ---------- log rendering ----------

_SECRET_KEYS = ("password", "token", "authorization", "secret")


def _render(value: Any) -> Any:
    """Redact credentials/tokens and render a payload for diagnostics."""
    if value is None:
        return None
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if any(secret in str(key).lower() for secret in _SECRET_KEYS)
            else _render(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_render(item) for item in value]
    if isinstance(value, (str, int, float, bool)):
        return value
    try:
        return jsonlib.dumps(value, default=str)
    except (TypeError, ValueError):
        return str(value)


def _render_request(value: Any) -> Any:
    """Render a request payload, summarized by default, full when opted in."""
    if _API_LOG_BODIES:
        return _render(value)
    if value is None:
        return None
    if isinstance(value, dict):
        return {"fields": len(value), "keys": sorted(str(k) for k in value)}
    return {"provided": True}


def _render_response(body: Any) -> Any:
    """Render a response envelope, full when opted in, summarized by default."""
    if _API_LOG_BODIES:
        return _render(body)
    return _summarize(body)


def _summarize(body: Any) -> Any:
    """Summarize an envelope without dumping patient records by default."""
    if not isinstance(body, dict):
        return {"type": type(body).__name__}
    summary: dict[str, Any] = {
        key: body.get(key) for key in ("statusCode", "message") if key in body
    }
    data = body.get("data")
    if isinstance(data, list):
        summary["data"] = {"type": "list", "items": len(data)}
    elif isinstance(data, dict):
        summary["data"] = {"type": "object", "keys": sorted(str(k) for k in data)}
    elif data is not None:
        summary["data"] = {"type": type(data).__name__}
    if "errors" in body:
        summary["errors"] = body.get("errors")
    return summary


def _try_json(response: httpx.Response) -> Any:
    """Decode a response without allowing invalid JSON to hide API logging."""
    try:
        return response.json()
    except ValueError:
        return None
