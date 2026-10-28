"""Portal lookup tools for the LLM (OCP: registered in TOOL_REGISTRY).

The booking tool needs ids the patient never says out loud (patientId,
providerId, service/procedureId). These small tools let the assistant look
them up conversationally:

* ``find_patient_by_phone``  — confirm a record exists before booking,
* ``list_services``          — offer the clinic's services,
* ``list_providers``         — offer the clinic's providers,
* ``find_available_slots``   — offer real open times for a date.

All of them return speakable strings and never raise into the LLM loop.
"""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional

from langchain_core.tools import tool

from modules.portal.portal_http_client import get_shared_portal_http_client
from modules.portal.dtos import GetAppointmentSlotsRequestDto, SlotViewType
from modules.portal.errors import PortalApiError, PortalAuthError, PortalNetworkError
from modules.portal.session import resolve_active_location_id, encrypt_location_id_for_portal
from modules.tools.portal_appointment_booking_repository import _find_item_by_case_insensitive_name, _map_patient_problem_to_service_keywords, _find_best_item_by_keyword_score, _format_date_for_speech
from modules.portal.token_store import get_portal_token
from modules.tools.tool_registry import TOOL_REGISTRY


def _map_portal_error_to_speakable_message(error: Exception) -> str:
    """Map portal failures to a sentence the assistant can say."""
    if isinstance(error, PortalAuthError):
        return (
            "Not signed in to the clinic portal — the front desk needs to sign "
            "in on the portal login page before I can do that."
        )
    if isinstance(error, PortalNetworkError):
        return "The clinic system is unreachable right now — please try again shortly."
    if isinstance(error, PortalApiError):
        return f"The clinic system could not complete that: {error.message}."
    return "That lookup failed due to a technical issue — please continue."


def _get_portal_session_token_or_none() -> Optional[str]:
    """Return the stored portal token, or ``None`` when nobody signed in."""
    return get_portal_token()


async def _resolve_active_location_for_session(token: str) -> str:
    """Active location id for the session (JWT claim -> token-details -> env)."""
    return await resolve_active_location_id(token)


@tool
async def find_patient_by_phone(mobile_number: str) -> str:
    """
    Check whether a patient exists in the clinic system by phone number.

    Call this BEFORE booking to confirm the patient has a record. The phone
    number must have 10-15 digits (with an optional leading +).

    Args:
        mobile_number: The patient's phone number (e.g. "9876543210").
    """
    digits = re.sub(r"\D", "", mobile_number or "")
    if not 10 <= len(digits) <= 15:
        return (
            "I need the patient's real phone number to look up the clinic record. "
            "Ask the patient for the 10 to 15 digit number; do not retry with a placeholder."
        )

    token = _get_portal_session_token_or_none()
    if not token:
        return _map_portal_error_to_speakable_message(PortalAuthError("no session"))
    try:
        envelope = await get_shared_portal_http_client().get_patient_list_data(token, search=digits)
    except (PortalApiError, PortalNetworkError) as error:
        return _map_portal_error_to_speakable_message(error)
    data = envelope.get("data") or {}
    patients = data.get("patients") if isinstance(data, dict) else None
    patients = patients or []
    match = next(
        (
            item
            for item in patients
            if re.sub(r"\D", "", str(item.get("mobileNumber") or ""))[-10:] == digits[-10:]
        ),
        None,
    )
    if match and match.get("id") is not None:
        return f"Patient found (patient id {match['id']})."
    return (
        f"No patient record found for phone number {mobile_number}. Ask the "
        "patient to double-check the number on their account."
    )


_NO_LOCATION = (
    "Your portal session has no clinic location attached — sign out and sign "
    "in again on the portal login page, then retry."
)


@tool
async def list_services() -> str:
    """List the dental services/procedures offered at this clinic location."""
    token = _get_portal_session_token_or_none()
    if not token:
        return _map_portal_error_to_speakable_message(PortalAuthError("no session"))
    try:
        envelope = await get_shared_portal_http_client().get_procedure_master_list(token)
    except (PortalApiError, PortalNetworkError) as error:
        return _map_portal_error_to_speakable_message(error)
    items = envelope.get("data") or []
    if not items:
        return "No services are configured for this location."
    lines = []
    for item in items:
        if item.get("procedureId") is None:
            continue
        desc = str(item.get("procedureDescription", "")).strip()
        providers = item.get("provider") or []
        operatories = item.get("operatoryId") or []
        operatory_names = [
            str(op.get("operatoryName", "")).strip()
            for op in operatories
            if isinstance(op, dict)
        ]
        suffix = ""
        if providers:
            suffix += f" (providers: {', '.join(str(p) for p in providers)})"
        if operatory_names:
            suffix += f" (operatories: {', '.join(operatory_names)})"
        lines.append(desc + suffix)
    if not lines:
        return "No services are configured for this location."
    return "Services: " + "; ".join(lines)


@tool
async def list_providers() -> str:
    """List the providers (dentists/hygienists) available at this location."""
    token = _get_portal_session_token_or_none()
    if not token:
        return _map_portal_error_to_speakable_message(PortalAuthError("no session"))
    try:
        envelope = await get_shared_portal_http_client().get_appointment_master_data(token)
    except (PortalApiError, PortalNetworkError) as error:
        return _map_portal_error_to_speakable_message(error)
    data = envelope.get("data") or {}
    items = data.get("providers") if isinstance(data, dict) else None
    items = items or []
    if not items:
        return "No providers are configured for this location."
    return "Providers: " + "; ".join(
        f"{item.get('providerName', '').strip()} (id {item.get('id')})"
        for item in items
        if item.get("id") is not None
    )


@tool
async def find_available_slots(appointment_date: str, service: Optional[str] = None) -> str:
    """
    Find available appointment time slots for a date at this clinic.

    Use this when the patient asks what times are open, before suggesting a
    specific time to book.

    Args:
        appointment_date: Date to check, as YYYY-MM-DD (e.g. "2026-09-25").
        service: Optional service name to scope the search (e.g. "Cleaning");
            defaults to the first configured service.
    """
    token = _get_portal_session_token_or_none()
    if not token:
        return _map_portal_error_to_speakable_message(PortalAuthError("no session"))

    api = get_shared_portal_http_client()
    try:
        location_id = await _resolve_active_location_for_session(token)
        if not location_id:
            return _NO_LOCATION
        envelope_services = await api.get_procedure_master_list(token)
        services = envelope_services.get("data") or []
        if not services:
            return "No services are configured for this location."
        match = _find_item_by_case_insensitive_name(services, service, "procedureDescription")
        if match is None and service:
            match = _find_best_item_by_keyword_score(services, _map_patient_problem_to_service_keywords(service))
        if match is None:
            match = services[0]
        payload = GetAppointmentSlotsRequestDto(
            viewType=SlotViewType.DAY,
            serviceId=int(match["procedureId"]),
            locationId=encrypt_location_id_for_portal(location_id),
            startDate=appointment_date,
            endDate=appointment_date,
        )
        envelope = await api.get_appointment_slots(payload)
    except (PortalApiError, PortalNetworkError) as error:
        return _map_portal_error_to_speakable_message(error)

    items = envelope.get("data") or []
    times = []
    for item in items:
        status = str(item.get("status") or "").lower()
        if status in ("booked", "closed", "unavailable", "blocked"):
            continue
        open_time = str(item.get("open") or "")[11:16]
        if not open_time or ":" not in open_time:
            continue
        try:
            speakable = datetime.strptime(open_time, "%H:%M").strftime("%I:%M %p").lstrip("0")
        except ValueError:
            speakable = open_time
        # Drop slots already past in clinic timezone when checking today
        from modules.common.settings import get_settings
        settings = get_settings()
        try:
            slot_dt = datetime.strptime(str(item.get("open") or "")[:10], "%Y-%m-%d")
            if slot_dt.date() == datetime.now(ZoneInfo(settings.clinic_timezone)).date():
                hm = open_time
                slot_hour, slot_min = int(hm.split(":")[0]), int(hm.split(":")[1])
                now_h, now_m = datetime.now(ZoneInfo(settings.clinic_timezone)).hour, datetime.now(ZoneInfo(settings.clinic_timezone)).minute
                if slot_hour < now_h or (slot_hour == now_h and slot_min < now_m + 15):
                    continue  # skip past / too-close slots
        except Exception:
            pass
        if speakable not in times:
            times.append(speakable)
        if len(times) >= 6:
            break
    if not times:
        return f"No open slots found on {_format_date_for_speech(appointment_date)}."
    return f"Open times on {_format_date_for_speech(appointment_date)}: " + ", ".join(times)


TOOL_REGISTRY.append(find_patient_by_phone)
TOOL_REGISTRY.append(list_services)
TOOL_REGISTRY.append(list_providers)
TOOL_REGISTRY.append(find_available_slots)


@tool
async def get_appointments(
    patient_name: Optional[str] = None,
    mobile_number: Optional[str] = None,
    appointment_date: Optional[str] = None,
) -> str:
    """Look up existing appointments for a patient or date at this clinic."""
    token = _get_portal_session_token_or_none()
    if not token:
        return _map_portal_error_to_speakable_message(PortalAuthError("no session"))

    payload: dict = {
        "perPage": 20,
        "page": 1,
        "filter": {
            "startDate": appointment_date or "",
            "endDate": appointment_date or "",
            "status": [],
            "providerName": [],
            "services": [],
        },
        "sortKey": "startTime",
        "sortValue": "asc",
        "search": (patient_name or mobile_number or "").strip(),
    }
    if not appointment_date:
        payload["filter"]["startDate"] = ""
        payload["filter"]["endDate"] = ""

    try:
        envelope = await get_shared_portal_http_client().list_appointments(token, payload)
    except (PortalApiError, PortalNetworkError) as error:
        return _map_portal_error_to_speakable_message(error)

    data = envelope.get("data") or {}
    items = data.get("data") if isinstance(data, dict) else None
    items = items or []
    if not items:
        return "No appointments found for that search."

    lines = []
    for item in items[:5]:
        patient = item.get("patient") or {}
        name = patient.get("name") or "Unknown"
        phone = patient.get("phoneNumber") or ""
        provider = item.get("providerName") or ""
        procedures = item.get("procedureList") or []
        service = ", ".join(
            str(p.get("procedureDescription") or p.get("procedureCode") or "")
            for p in procedures
        )
        start = str(item.get("startTime") or "")
        # Convert ISO to speakable if possible.
        spoken = start
        try:
            from datetime import datetime
            from zoneinfo import ZoneInfo

            dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
            spoken = dt.strftime("%B %d, %Y at %I:%M %p").lstrip("0").replace(" 0", " ")
        except ValueError:
            pass
        status = item.get("bookingStatus") or ""
        lines.append(
            f"{name} ({phone}) - {service} with {provider} on {spoken} [{status}]"
        )
    return "Appointments: " + "; ".join(lines)


TOOL_REGISTRY.append(get_appointments)
