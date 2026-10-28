"""Booking through the customer-portal API (DIP: real AppointmentRepository).

Implements the ``AppointmentRepository`` contract used by the
``book_appointment`` LangChain tool:

1. resolve the patient by mobile number (portal ``/patient/by-mobile``),
2. resolve service + provider names to ids (location lookups),
3. combine date + time into the required start/end window,
4. call the authenticated ``/appointments/appointmentBook`` endpoint.

The JWT comes from ``portal_token_store`` (filled by the login route or the
websocket cookie handshake) and is replayed as ``Authorization: Bearer``.
Every failure path returns a sentence the assistant can speak — no
exceptions escape to the LLM loop.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import Optional

from modules.common.backend_contracts import AppointmentRepository as IAppointmentRepository
from modules.common.settings import get_settings
from modules.portal.portal_http_client import CustomerPortalHttpClient, get_shared_portal_http_client
from modules.portal.dtos import (
    AppointmentBookRequestDTO,
    AppointmentStatusType,
    GetAppointmentSlotsRequestDto,
    SlotViewType,
)
from modules.portal.errors import PortalApiError, PortalAuthError, PortalNetworkError
from modules.portal.session import resolve_active_location_id, encrypt_location_id_for_portal
from modules.portal.token_store import PortalTokenStore, portal_token_store
from modules.common.redact import redact

logger = logging.getLogger("booking")

# Tolerated input formats for the conversational date/time arguments.
_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%d-%m-%Y", "%B %d %Y", "%b %d %Y", "%B %d, %Y", "%b %d, %Y")
_TIME_FORMATS = ("%I:%M %p", "%I:%M%p", "%H:%M")

_FALLBACK_MESSAGES = {
    "auth": (
        "Not signed in to the clinic portal. Ask the staff member to sign in "
        "on the portal login page, then repeat the booking."
    ),
    "network": (
        "The clinic system is unreachable right now. Apologize briefly, tell "
        "the patient booking is temporarily unavailable, and try again shortly."
    ),
    "providers": (
        "No providers are configured for this location, so the appointment "
        "cannot be booked yet. Tell the patient and ask them to call the front desk."
    ),
    "services": (
        "No services are configured for this location, so the appointment "
        "cannot be booked yet. Tell the patient and ask them to call the front desk."
    ),
    "location": (
        "Your portal session has no clinic location attached. Ask the staff "
        "to sign out and sign in again on the portal login page, then repeat "
        "the booking."
    ),
}


def _parse_appointment_start_and_end(
    appointment_date: str, appointment_time: str, duration_minutes: int
) -> tuple[datetime, datetime]:
    """Parse conversational date/time strings into the start/end window."""
    import re as _re

    date_text = appointment_date.strip()
    # Strip ordinal suffixes so "October 28th 2026" parses like "October 28 2026".
    date_text = _re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", date_text, flags=_re.IGNORECASE)
    time_text = appointment_time.strip().upper()

    date_value: Optional[datetime] = None
    for fmt in _DATE_FORMATS:
        try:
            date_value = datetime.strptime(date_text, fmt)
            break
        except ValueError:
            continue
    if date_value is None:
        raise ValueError(f"Unrecognised appointment date: {appointment_date!r}")

    time_value: Optional[datetime] = None
    for fmt in _TIME_FORMATS:
        try:
            time_value = datetime.strptime(time_text, fmt)
            break
        except ValueError:
            continue
    if time_value is None:
        raise ValueError(f"Unrecognised appointment time: {appointment_time!r}")

    start = date_value.replace(
        hour=time_value.hour, minute=time_value.minute, second=0, microsecond=0
    )
    return start, start + timedelta(minutes=duration_minutes)




def _format_date_for_speech(date_str: str) -> str:
    """Parse a date string and return a spoken form like 'October 28th'."""
    import re as _re

    text = (date_str or "").strip()
    text = _re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", text, flags=_re.IGNORECASE)
    for fmt in _DATE_FORMATS:
        try:
            value = datetime.strptime(text, fmt)
            day = value.day
            suffix = "th"
            if day % 10 == 1 and day != 11:
                suffix = "st"
            elif day % 10 == 2 and day != 12:
                suffix = "nd"
            elif day % 10 == 3 and day != 13:
                suffix = "rd"
            return value.strftime("%B ") + f"{day}{suffix}"
        except ValueError:
            continue
    return text


def _format_time_for_speech(t: str) -> str:
    """Return a 12-hour speakable time like '10:30 AM'."""
    value = (t or "").strip().upper()
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(value, fmt).strftime("%I:%M %p").lstrip("0")
        except ValueError:
            continue
    return value

def _find_item_by_case_insensitive_name(items: list[dict], wanted: Optional[str], name_key: str) -> Optional[dict]:
    """Case-insensitive substring match of ``wanted`` against ``name_key``."""
    if not wanted:
        return None
    needle = wanted.strip().lower()
    if not needle:
        return None
    for item in items:
        if needle in str(item.get(name_key, "")).lower():
            return item
    return None


# Patient-phrasing -> catalog search keywords, most specific signal first.
# Generic words (e.g. "exam") are only tie-breakers: ``_find_best_item_by_keyword_score`` picks
# the service with the most keyword hits instead of the first hit.
_PROBLEM_TERMS: dict[str, tuple[str, ...]] = {
    "clean": ("clean", "hygiene", "prophy"),
    "toothache": ("toothache", "pain", "emergency", "exam", "filling", "restor"),
    "pain": ("pain", "emergency", "exam"),
    "hurt": ("pain", "emergency", "exam"),
    "cavity": ("cavity", "filling", "restor", "exam"),
    "molar": ("pain", "emergency", "exam", "filling", "restor"),
    "tooth": ("pain", "emergency", "exam", "filling", "restor"),
    "teeth": ("pain", "emergency", "exam", "filling", "restor"),
    "cavity's": ("cavity", "filling", "restor", "exam"),
    "broken": ("broken", "fracture", "emergency", "restor", "exam"),
    "chipped": ("chip", "emergency", "restor", "exam"),
    "checkup": ("checkup", "exam", "check", "comprehensive"),
    "check-up": ("checkup", "exam", "check", "comprehensive"),
    "whitening": ("whiten", "cosmetic"),
    "braces": ("orthodont", "braces"),
    "aligner": ("orthodont", "aligner"),
    "implant": ("implant", "surgical"),
    "extraction": ("extract", "surgical"),
    "removed": ("extract", "surgical"),
    "gum": ("periodont", "gum", "hygiene"),
    "sensitive": ("sensitive", "desensit", "exam"),
}

# Stop words for the free-text fallback: only substantive tokens (>= 4 chars)
# are used, so "my eye hurts" cannot match an unrelated catalog entry.
_FALLBACK_STOPWORDS = frozenset({
    "the", "and", "with", "for", "have", "need", "want", "help", "please",
    "appointment", "dentist", "clinic", "today", "yesterday", "tomorrow",
    "when", "what", "where", "book", "make", "get", "can", "that", "this",
})


def _find_best_item_by_keyword_score(items: list[dict], keywords: tuple[str, ...]) -> Optional[dict]:
    """Return the item whose description matches the most keywords."""
    best: Optional[dict] = None
    best_score = 0
    for item in items:
        description = str(item.get("procedureDescription", "")).lower()
        score = sum(1 for keyword in keywords if keyword in description)
        if score > best_score:
            best, best_score = item, score
    return best


def _map_patient_problem_to_service_keywords(problem: str) -> tuple[str, ...]:
    """Pick search keywords for a free-text patient problem."""
    normalized = problem.lower()
    for phrase, mapped in _PROBLEM_TERMS.items():
        if phrase in normalized:
            return mapped
    return tuple(
        token
        for token in normalized.split()
        if len(token) >= 4 and token not in _FALLBACK_STOPWORDS
    )


class PortalAppointmentBookingRepository(IAppointmentRepository):
    """Books real appointments through the customer-portal REST API."""

    def __init__(
        self,
        client: Optional[CustomerPortalHttpClient] = None,
        token_store: Optional[PortalTokenStore] = None,
    ) -> None:
        """Inject the API client/token store for tests; production uses globals."""
        self._client = client
        self._token_store = token_store or portal_token_store

    def _api(self) -> CustomerPortalHttpClient:
        """Return the injected client or the process-wide portal client."""
        return self._client or get_shared_portal_http_client()

    async def book(
        self,
        patient_name: str,
        appointment_date: str,
        appointment_time: str,
        mobile_number: str,
        *,
        provider: Optional[str] = None,
        service: Optional[str] = None,
        birth_date: Optional[str] = None,
        gender: Optional[str] = None,
    ) -> str:
        """Book one appointment; always returns a patient-facing result string."""
        token = self._token_store.get()
        if not token:
            logger.warning("failed: portal auth token is missing")
            return _FALLBACK_MESSAGES["auth"]

        print(f"===== BOOKING START ===== token={token} patient_name={patient_name} mobile={mobile_number} date={appointment_date} time={appointment_time} provider={provider} service={service}")
        settings = get_settings()
        api = self._api()

        try:
            print(f"===== BOOKING: finding patient by mobile ===== mobile={mobile_number} name={patient_name} birth={birth_date}")
            patient = await self._find_patient_by_mobile_number(token, mobile_number, patient_name, birth_date)
            print(f"===== BOOKING: patient lookup result ===== patient={patient}")
            if isinstance(patient, dict) and patient.get("ambiguous"):
                print(f"===== BOOKING: ambiguous patient match ===== count={patient.get('count')}")
                return (
                    "I found more than one patient with that phone number. "
                    "Please confirm the patient's full name and date of birth "
                    "so I can pick the right record."
                )
            if patient is None:
                if birth_date and gender:
                    try:
                        name_parts = (patient_name or "").strip().split()
                        first = name_parts[0] if name_parts else patient_name or "Patient"
                        if len(name_parts) < 2:
                            return "I need the patient's full first and last name to create a record."
                        last = " ".join(name_parts[1:])
                        payload = {
                            "firstName": first,
                            "lastName": last,
                            "birthDate": birth_date,
                            "gender": gender,
                            "mobileNumber": re.sub(r"\D", "", mobile_number or ""),
                        }
                        envelope = await self._api().create_patient(token, payload)
                        print(f"===== BOOKING: create_patient response ===== response={envelope}")
                        data = envelope.get("data") or {}
                        if isinstance(data, dict) and data.get("patientId"):
                            patient = {
                                "patientId": data["patientId"],
                                "name": patient_name,
                                "mobileNumber": mobile_number,
                            }
                    except (PortalApiError, PortalNetworkError) as error:
                        logger.warning("patient creation failed: %r", error)
                        patient = None
                    except Exception as error:
                        logger.warning("patient creation failed: %r", error, exc_info=True)
                        patient = None
                if patient is None:
                    logger.warning("failed: patient record was not found")
                    return (
                        f"No patient record found for phone number {redact(mobile_number)}. "
                        "To book, I'll need the patient's date of birth (YYYY-MM-DD) "
                        "and gender (male/female/other) to create a record, or "
                        "they can register at the front desk."
                    )

            # Location comes from the JWT session (claim -> token-details),
            # matching the location the portal binds to this token server-side.
            location_id = await resolve_active_location_id(
                token, client=api, store=self._token_store
            )
            print(f"===== BOOKING: location resolved ===== location_id={location_id}")
            if not location_id:
                logger.warning("failed: no clinic location resolved")
                return _FALLBACK_MESSAGES["location"]

            service_id, service_name = await self._resolve_service_id_from_patient_problem(api, service)
            print(f"===== BOOKING: service resolved ===== service_id={service_id} service_name={service_name}")
            if service_id is None:
                logger.warning("service mapping failed for patient problem=%r", service)
                if isinstance(service_name, str) and service_name.startswith("__SERVICES__:"):
                    names = service_name.split(":", 1)[1]
                    return (
                        "I could not match that problem to a configured clinic service. "
                        "Available services: " + names + ". Ask the patient which they need."
                    )
                return (
                    "I could not match that problem to a configured clinic service. "
                    "Ask one short follow-up question about the patient's symptoms, "
                    "or ask the front desk to help choose the right service."
                )

            master_envelope = await api.get_appointment_master_data(token)
            print(f"===== BOOKING: master_data response ===== response={master_envelope}")
            master_data = master_envelope.get("data") or {}

            provider_id = await self._resolve_provider_id_for_service(api, token, provider, service_id=service_id, service_name=service_name, master_data=master_data)
            print(f"===== BOOKING: provider resolved ===== provider_id={provider_id} provider={provider}")
            if provider_id is None:
                logger.warning("failed: no provider is configured/matched")
                return _FALLBACK_MESSAGES["providers"]

            operatory_id = await self._resolve_operatory_id_for_service(api, token, service_id=service_id, service_name=service_name, master_data=master_data)

            provider_name = provider
            if not provider_name:
                provider_items = master_data.get("providers") if isinstance(master_data, dict) else []
                provider_items = provider_items or []
                selected_provider = next(
                    (item for item in provider_items
                     if str(item.get("id")) == str(provider_id)),
                    {},
                )
                provider_name = str(selected_provider.get("providerName") or "clinic-selected provider")
            logger.info(
                "selection: service=%r (id=%s), provider=%r (id=%s); "
                "requested_service=%r, requested_provider=%r",
                service_name, service_id, provider_name, provider_id, service, provider,
            )

            # Resolve candidate appointment times. If the caller gave an exact
            # time, try it first; otherwise (or on conflict) fall back to the
            # clinic's available slots for that date and pick one.
            def _period_of(text: str) -> str:
                lowered = (text or "").lower()
                if "morning" in lowered:
                    return "morning"
                if "afternoon" in lowered:
                    return "afternoon"
                if "evening" in lowered:
                    return "evening"
                return "any"

            def _is_exact_time(text: str) -> bool:
                value = (text or "").strip().upper()
                for fmt in _TIME_FORMATS:
                    try:
                        datetime.strptime(value, fmt)
                        return True
                    except ValueError:
                        continue
                return False

            async def _fetch_available_slot_times() -> list[str]:
                try:
                    slot_payload = GetAppointmentSlotsRequestDto(
                        viewType=SlotViewType.DAY,
                        serviceId=int(service_id),
                        locationId=encrypt_location_id_for_portal(location_id),
                        startDate=appointment_date,
                        endDate=appointment_date,
                    )
                    slot_envelope = await api.get_appointment_slots(slot_payload)
                except (PortalApiError, PortalNetworkError):
                    return []
                items = slot_envelope.get("data") or []
                times: list[str] = []
                for item in items:
                    status = str(item.get("status") or "").lower()
                    if status in ("booked", "closed", "unavailable", "blocked"):
                        continue
                    open_time = str(item.get("open") or "")[11:16]
                    if open_time and ":" in open_time:
                        times.append(open_time)
                return sorted(set(times))

            period = _period_of(appointment_time)
            candidate_times: list[str] = []
            if _is_exact_time(appointment_time):
                candidate_times = [appointment_time.strip().upper()]
            else:
                available = await _fetch_available_slot_times()
                if period != "any":
                    def _in_period(value: str) -> bool:
                        try:
                            hour = datetime.strptime(value, "%H:%M").hour
                        except ValueError:
                            return True
                        if period == "morning":
                            return hour < 12
                        if period == "afternoon":
                            return 12 <= hour < 17
                        if period == "evening":
                            return hour >= 17
                        return True
                    available = [t for t in available if _in_period(t)]
                for t in available:
                    if t not in candidate_times:
                        candidate_times.append(t)

            if not candidate_times:
                return (
                    f"No open slots found on {_format_date_for_speech(appointment_date)}. "
                    "Apologize and ask the patient to choose another date."
                )

            if not _is_exact_time(appointment_time) and candidate_times:
                speakable = []
                for t in candidate_times[:2]:
                    try:
                        speakable.append(datetime.strptime(t, "%H:%M").strftime("%I:%M %p").lstrip("0"))
                    except ValueError:
                        speakable.append(t)
                return (
                    "Available times on " + _format_date_for_speech(appointment_date) + ": "
                    + ", ".join(speakable)
                    + ". Ask the patient which they prefer."
                )

            # Idempotency: don't double-book on a retry after a timeout.
            if patient and isinstance(patient, dict) and patient.get("patientId") and candidate_times:
                if await self._has_existing_appointment(token, api, patient, appointment_date, candidate_times[0], service_id):
                    return (
                        f"An appointment for {patient_name} on {_format_date_for_speech(appointment_date)} "
                        f"at {_format_time_for_speech(candidate_times[0])} is already booked."
                    )

            lock = await self._session_booking_lock(token)
            async with lock:
                pass

            print(f"===== BOOKING: attempting appointment book loop ===== patient_id={patient.get('patientId') if isinstance(patient, dict) else patient} service_id={service_id} provider_id={provider_id} date={appointment_date} candidate_times={candidate_times}")
            last_error: Optional[str] = None
            for attempt_time in candidate_times[:5]:
                try:
                    start, end = _parse_appointment_start_and_end(
                        appointment_date,
                        attempt_time,
                        settings.portal_appointment_duration_minutes,
                    )
                    payload = self._build_appointment_booking_payload(patient, service_id, provider_id, start, end, operatory_id=operatory_id)
                    print(f"===== BOOKING: book_appointment payload ===== payload={payload}")
                    envelope = await api.book_appointment(token, payload)
                    print(f"===== BOOKING: book_appointment response ===== envelope={envelope}")
                    data = envelope.get("data") or {}
                    appointment_id = data.get("id") if isinstance(data, dict) else None
                    logger.info(
                        "succeeded: patient_id=%s service=%r provider=%r appointment_id=%s time=%s",
                        patient.get("patientId"), service_name, provider_name, appointment_id, attempt_time,
                    )
                    booked_for = patient_name
                    confirmation = f"Appointment booked for {booked_for} on {_format_date_for_speech(appointment_date)} at {_format_time_for_speech(attempt_time)}"
                    if service_name:
                        confirmation += f" for {service_name}"
                    if provider_name:
                        confirmation += f" with {provider_name}"
                    if appointment_id is not None:
                        confirmation += f" (appointment id {appointment_id})"
                    return confirmation + "."
                except PortalAuthError:
                    print(f"===== BOOKING: PortalAuthError for time={attempt_time}")
                    logger.warning("failed: portal authentication rejected appointment booking")
                    self._token_store.clear()
                    return _FALLBACK_MESSAGES["auth"]
                except PortalNetworkError:
                    print(f"===== BOOKING: PortalNetworkError for time={attempt_time}")
                    logger.warning("failed: portal network error during appointment booking")
                    return _FALLBACK_MESSAGES["network"]
                except PortalApiError as error:
                    detail = error.errors or error.message
                    print(f"===== BOOKING: PortalApiError for time={attempt_time} detail={detail} error={error}")
                    logger.warning("appointmentBook failed at %s: %r", attempt_time, error)
                    last_error = detail
                    # Conflict: do not silently book another time. Offer open slots.
                    if "already exists" in str(detail).lower() or "conflict" in str(detail).lower():
                        slots = await _fetch_available_slot_times()
                        speakable = []
                        for t in slots[:3]:
                            try:
                                speakable.append(datetime.strptime(t, "%H:%M").strftime("%I:%M %p").lstrip("0"))
                            except ValueError:
                                speakable.append(t)
                        if speakable:
                            return (
                                "That time is taken. Open times that day: "
                                + ", ".join(speakable)
                                + ". Ask the patient which they prefer."
                            )
                        return (
                            f"The appointment could not be booked ({detail}). Apologize, "
                            "tell the patient the slot may no longer be available, and ask "
                            "them to pick another time."
                        )
                    return (
                        f"The appointment could not be booked ({detail}). Apologize, "
                        "tell the patient the slot may no longer be available, and ask "
                        "them to pick another time."
                    )
                except ValueError as error:
                    print(f"===== BOOKING: ValueError for time={attempt_time} error={error}")
                    logger.warning("invalid time %s: %r", attempt_time, error)
                    continue
                except Exception as error:
                    print(f"===== BOOKING: Exception for time={attempt_time} error={error}")
                    logger.warning("booking failed at %s: %r", attempt_time, error, exc_info=True)
                    return "Booking failed due to a technical issue. Apologize briefly and ask the patient to confirm the details so you can try again."

            return (
                f"The appointment could not be booked ({last_error}). All attempted "
                "slots were unavailable. Apologize and ask the patient to pick "
                "another date or time."
            )
        except PortalAuthError:
            logger.warning("failed during preparation: portal authentication rejected")
            self._token_store.clear()
            return _FALLBACK_MESSAGES["auth"]
        except PortalNetworkError:
            logger.warning("failed during preparation: portal network error")
            return _FALLBACK_MESSAGES["network"]
        except (PortalApiError, ValueError) as error:
            logger.warning("booking prep failed: %r", error)
            return (
                f"The clinic system rejected the booking details ({error}). "
                "Apologize briefly and ask the patient to confirm name, date, "
                "time, and phone number before trying again."
            )

    # ---------- lookups ----------
    async def _find_patient_by_mobile_number(
        self, token: str, mobile_number: str, patient_name: str, birth_date: Optional[str] = None
    ) -> Optional[dict]:
        """Return the patient record for ``mobile_number`` or ``None`` (paginated)."""
        digits = re.sub(r"\D", "", mobile_number or "")
        matches: list[dict] = []
        page = 1
        while True:
            envelope = await self._api().get_patient_list_data(token, page=page, limit=20, search=digits)
            data = envelope.get("data") or {}
            patients = data.get("patients") if isinstance(data, dict) else None
            patients = patients or []
            for item in patients:
                if re.sub(r"\D", "", str(item.get("mobileNumber") or ""))[-10:] != digits[-10:]:
                    continue
                matches.append(item)
            pagination = data.get("pagination") if isinstance(data, dict) else None
            has_more = bool(pagination.get("hasMore")) if isinstance(pagination, dict) else False
            if not has_more:
                break
            page += 1

        if not matches:
            return None

        # Require a full-name match AND a date-of-birth match. No subset
        # matching: "John" must not match "John Smith" sharing a phone.
        name_needle = (patient_name or "").strip().lower()
        filtered = list(matches)
        if name_needle:
            exact = [
                m for m in filtered
                if str(m.get("displayName") or m.get("patientName") or "").strip().lower() == name_needle
            ]
            filtered = exact
        if birth_date:
            dob_filtered = [
                m for m in filtered
                if str(m.get("birthDate") or "")[:10] == str(birth_date)[:10]
            ]
            filtered = dob_filtered
        if not filtered:
            return None
        if len(filtered) > 1:
            return {"ambiguous": True, "count": len(filtered)}
        chosen = filtered[0]

        name = chosen.get("displayName") or chosen.get("patientName") or patient_name
        return {
            "patientId": chosen["id"],
            "name": name,
            "mobileNumber": chosen.get("mobileNumber"),
            "birthDate": chosen.get("birthDate"),
            "preferredName": chosen.get("preferredName"),
        }


    async def _resolve_service_id_from_patient_problem(
        self, api: CustomerPortalHttpClient, service: Optional[str]
    ) -> tuple[Optional[int], str]:
        """Map the patient's problem to the closest configured service."""
        token = self._token_store.get() or ""
        page = 1
        all_items: list[dict] = []
        while True:
            try:
                envelope = await api.get_procedure_master_list(token, page=page, per_page=20)
            except (PortalApiError, PortalNetworkError) as error:
                logger.warning("service catalog request failed: %r", error)
                raise
            items = envelope.get("data") or []
            all_items.extend(items)
            meta = envelope.get("meta") or {}
            total = int(meta.get("totalCount") or 0)
            if not items or page * 20 >= total or not meta:
                break
            page += 1
        if not all_items:
            return None, ""
        match = _find_item_by_case_insensitive_name(all_items, service, "procedureDescription")
        if match is None and service:
            match = _find_best_item_by_keyword_score(all_items, _map_patient_problem_to_service_keywords(service))
        if match is None:
            # The patient described an issue, but configured portal data has
            # no matching service. If the catalog is small, return the list of
            # service names so the model can ask the caller to choose.
            if len(all_items) <= 3:
                names = ", ".join(str(i.get("procedureDescription", "")).strip() for i in items if i.get("procedureDescription"))
                return None, "__SERVICES__:" + names
            return None, ""
        return int(match["procedureId"]), str(match.get("procedureDescription", ""))

    async def _resolve_provider_id_for_service(
        self, api: CustomerPortalHttpClient, token: str, provider: Optional[str],
        service_id: Optional[int] = None, service_name: Optional[str] = None,
        master_data: Optional[dict] = None,
    ) -> Optional[int]:
        """Use a requested provider, otherwise let the clinic choose by default."""
        if master_data is None:
            try:
                envelope = await api.get_appointment_master_data(token)
            except (PortalApiError, PortalNetworkError) as error:
                logger.warning("provider catalog request failed: %r", error)
                raise
            master_data = envelope.get("data") or {}
        data = master_data
        reasons = data.get("reasons") if isinstance(data, dict) else None
        reasons = reasons or []
        # Prefer providers linked to the matched service/reason.
        linked: list[dict] = []
        for reason in reasons:
            if service_id is not None and int(reason.get("id", -1)) == int(service_id):
                linked = reason.get("providers") or []
                break
            if service_name and str(reason.get("reasonName", "")).lower() == str(service_name).lower():
                linked = reason.get("providers") or []
                break
        items = linked if linked else (data.get("providers") if isinstance(data, dict) else None) or []
        if not items:
            return None
        if provider:
            match = _find_item_by_case_insensitive_name(items, provider, "providerName")
        else:
            match = items[0]
        if match is None:
            return None
        return int(match["id"])

    async def _resolve_operatory_id_for_service(
        self, api: CustomerPortalHttpClient, token: str,
        service_id: Optional[int] = None, service_name: Optional[str] = None,
        master_data: Optional[dict] = None,
    ) -> Optional[int]:
        """Pick an operatory valid for the matched service, if available."""
        if master_data is None:
            try:
                envelope = await api.get_appointment_master_data(token)
            except (PortalApiError, PortalNetworkError) as error:
                logger.warning("operatory catalog request failed: %r", error)
                raise
            master_data = envelope.get("data") or {}
        data = master_data
        reasons = data.get("reasons") if isinstance(data, dict) else None
        reasons = reasons or []
        for reason in reasons:
            if service_id is not None and int(reason.get("id", -1)) == int(service_id):
                ops = reason.get("operatories") or []
                if ops:
                    return int(ops[0].get("id"))
                break
            if service_name and str(reason.get("reasonName", "")).lower() == str(service_name).lower():
                ops = reason.get("operatories") or []
                if ops:
                    return int(ops[0].get("id"))
                break
        operatories = data.get("operatories") if isinstance(data, dict) else None
        operatories = operatories or []
        if operatories:
            return int(operatories[0].get("id"))
        return None

    async def _session_booking_lock(self, token: str):
        global _BOOKING_LOCKS, _BOOKING_LOCKS_GUARD
        if "_BOOKING_LOCKS" not in globals():
            _BOOKING_LOCKS = {}
            _BOOKING_LOCKS_GUARD = asyncio.Lock()
        async with _BOOKING_LOCKS_GUARD:
            if token not in _BOOKING_LOCKS:
                _BOOKING_LOCKS[token] = asyncio.Lock()
            return _BOOKING_LOCKS[token]

    async def _has_existing_appointment(
        self, token: str, api: CustomerPortalHttpClient, patient: dict,
        appointment_date: str, appointment_time: str, service_id: Optional[int],
    ) -> bool:
        try:
            payload = {
                "perPage": 20,
                "page": 1,
                "filter": {
                    "startDate": appointment_date,
                    "endDate": appointment_date,
                    "status": [],
                    "providerName": [],
                    "services": [],
                },
                "sortKey": "startTime",
                "sortValue": "asc",
                "search": str(patient.get("name") or ""),
            }
            envelope = await api.list_appointments(token, payload)
        except Exception:
            return False
        data = envelope.get("data") or {}
        items = data.get("data") if isinstance(data, dict) else None
        items = items or []
        target_patient = str(patient.get("patientId") or patient.get("id") or "")
        target_hm = None
        for fmt in _TIME_FORMATS:
            try:
                target_hm = datetime.strptime(appointment_time.strip().upper(), fmt).strftime("%H:%M")
                break
            except ValueError:
                continue
        for item in items:
            p = item.get("patient") or {}
            pid = str(p.get("id") or p.get("patientId") or "")
            if target_patient and pid and pid != target_patient:
                continue
            start = str(item.get("startTime") or "")
            existing_hm = start[11:16] if len(start) >= 16 else ""
            try:
                existing_hm = datetime.fromisoformat(start.replace("Z", "+00:00")).strftime("%H:%M")
            except ValueError:
                pass
            if target_hm and existing_hm and target_hm != existing_hm:
                continue
            if service_id is not None:
                procs = item.get("procedureList") or []
                if not any(int(x.get("procedureId") or x.get("id") or -1) == int(service_id) for x in procs):
                    continue
            return True
        return False

    @staticmethod
    def _build_appointment_booking_payload(
        patient: dict,
        service_id: int,
        provider_id: int,
        start: datetime,
        end: datetime,
        operatory_id: Optional[int] = None,
    ) -> dict:
        """Assemble the portal's ``AppointmentBookRequestDTO`` body.

        Field-for-field mirror: ``modules/portal/dtos.py`` (which tracks
        ``customer-portal/src/modules/appointments/dto/appointment-book.request.dto.ts``).
        ``reason`` is the resolved procedure id; ``appointmentStatus`` must be
        a member of the portal's ``AppointmentStatusType`` enum.
        """
        dto = AppointmentBookRequestDTO(
            patientId=int(patient["patientId"]),
            providerId=int(provider_id),
            operatoryId=operatory_id,
            reason=[int(service_id)],
            startTime=start.isoformat(),
            endTime=end.isoformat(),
            appointmentStatus=AppointmentStatusType.CONFIRMED,
            preferredName=patient.get("preferredName") or patient.get("name") or None,
            mobileNumber=patient.get("mobileNumber") or None,
            dateOfBirth=patient.get("birthDate") or None,
            appointmentDescription="Booked by voice assistant",
        )
        return dto.to_payload()
