"""Per-session booking state shared by the turn manager and the booking tool.

The turn manager owns the writes (readback_done, user_confirmed, offered_slots).
The tool only reads them, so the model cannot self-confirm a booking.

Usage in the turn manager:
    state = BookingState()
    set_booking_state(state)   # once, at session start, before any LLM turn
"""

from __future__ import annotations

import re
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class BookingState:
    # Set True when the assistant has read back all details and asked "Is that correct?"
    readback_done: bool = False
    # Set True only when the NEXT caller turn is a clear yes with no correction.
    user_confirmed: bool = False
    # Normalized "HH:MM" strings from the last find_available_slots result.
    offered_slots: set[str] = field(default_factory=set)
    # A slot explicitly selected by the caller from ``offered_slots``.
    selected_slot: Optional[str] = None
    # Caller-owned collection state. These flags are updated from caller
    # utterances, never from model-generated tool arguments.
    patient_name_provided: bool = False
    birth_date_provided: bool = False
    mobile_number_provided: bool = False
    gender_provided: bool = False
    visit_reason_provided: bool = False
    appointment_date_provided: bool = False
    expected_field: Optional[str] = None

    def reset_confirmation(self) -> None:
        self.readback_done = False
        self.user_confirmed = False

    def replace_offered_slots(self, slots: list[str]) -> None:
        """Store the latest availability result and require a new selection."""
        self.offered_slots = {
            normalized
            for slot in slots
            if (normalized := normalize_time(slot)) is not None
        }
        self.selected_slot = None
        self.reset_confirmation()

    def record_caller_utterance(self, text: str) -> None:
        """Record an explicit caller slot choice and confirmation response."""
        lowered = (text or "").lower()
        expected = self.expected_field
        self.expected_field = None
        meaningful = bool(re.search(r"[a-z0-9]", lowered))

        if meaningful and expected == "patient_name":
            self.patient_name_provided = True
        elif meaningful and expected == "birth_date":
            self.birth_date_provided = True
        elif meaningful and expected == "mobile_number":
            self.mobile_number_provided = True
        elif meaningful and expected == "gender":
            self.gender_provided = True
        elif meaningful and expected == "visit_reason":
            self.visit_reason_provided = True
        elif meaningful and expected == "appointment_date":
            self.appointment_date_provided = True

        if re.search(r"\b(my name is|i am|i'm)\s+[a-z][a-z' -]+", lowered):
            self.patient_name_provided = True
        if "born" in lowered or re.search(
            r"\b(?:\d{1,2}[/-]){2}\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b",
            lowered,
        ):
            self.birth_date_provided = True
        if len(re.sub(r"\D", "", text or "")) >= 10:
            self.mobile_number_provided = True
        if re.search(r"\b(male|female|man|woman|nonbinary|non-binary|other)\b", lowered):
            self.gender_provided = True
        if re.search(
            r"\b(tooth|teeth|pain|ache|cavity|filling|cleaning|checkup|check-up|"
            r"broken|chipped|swelling|gum|whitening|braces|implant|extraction|"
            r"sensitive|routine visit|general check)\b",
            lowered,
        ):
            self.visit_reason_provided = True
        # A birth-date answer must not also become the appointment date.
        if expected != "birth_date" and re.search(
            r"\b(today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b|"
            r"\b(?:january|february|march|april|may|june|july|august|september|october|november|december)\b|"
            r"\b\d{4}-\d{2}-\d{2}\b",
            lowered,
        ):
            self.appointment_date_provided = True
        if self.offered_slots:
            time_mentions = re.findall(
                r"\b\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)\b|\b(?:[01]?\d|2[0-3]):[0-5]\d\b",
                text or "",
                flags=re.IGNORECASE,
            )
            selected = next(
                (
                    normalized
                    for mention in time_mentions
                    if (normalized := normalize_time(mention)) in self.offered_slots
                ),
                None,
            )
            ordered = sorted(self.offered_slots)
            if selected is None and ordered:
                if re.search(r"\b(first|earliest)\b", lowered):
                    selected = ordered[0]
                elif len(ordered) > 1 and re.search(r"\b(second|next one)\b", lowered):
                    selected = ordered[1]
            if selected is not None:
                self.selected_slot = selected
                self.reset_confirmation()

        if self.readback_done:
            affirmative = bool(
                re.search(r"\b(yes|yeah|yep|correct|right|sure|confirm|confirmed|exactly)\b", lowered)
            )
            negative = bool(
                re.search(r"\b(no|not|wrong|incorrect|change|wait|different)\b", lowered)
            )
            self.user_confirmed = affirmative and not negative
            if not self.user_confirmed:
                self.reset_confirmation()

    def record_assistant_utterance(self, text: str) -> None:
        """Remember the first field question actually spoken to the caller."""
        first_question = (text or "").split("?", 1)[0].lower()
        if "full name" in first_question or "your name" in first_question:
            self.expected_field = "patient_name"
        elif "date of birth" in first_question or "when were you born" in first_question:
            self.expected_field = "birth_date"
        elif "mobile number" in first_question or "phone number" in first_question:
            self.expected_field = "mobile_number"
        elif "gender" in first_question:
            self.expected_field = "gender"
        elif any(term in first_question for term in ("reason for", "problem", "symptom", "bringing you in")):
            self.expected_field = "visit_reason"
        elif any(term in first_question for term in ("which day", "what day", "preferred date")):
            self.expected_field = "appointment_date"

    def first_missing_information(self) -> Optional[str]:
        """Return the first caller-owned field required before availability."""
        required = (
            ("full name", self.patient_name_provided),
            ("date of birth", self.birth_date_provided),
            ("mobile number", self.mobile_number_provided),
            ("gender", self.gender_provided),
            ("reason for the visit", self.visit_reason_provided),
            ("preferred appointment date", self.appointment_date_provided),
        )
        return next((name for name, supplied in required if not supplied), None)


_state_var: ContextVar[Optional[BookingState]] = ContextVar("booking_state", default=None)


def set_booking_state(state: BookingState) -> None:
    _state_var.set(state)


def get_booking_state() -> BookingState:
    state = _state_var.get()
    if state is None:
        # No turn manager attached: return a fresh state so booking is rejected, never allowed.
        state = BookingState()
        _state_var.set(state)
    return state


_TIME_RE = re.compile(r"^\s*(\d{1,2})(?::(\d{2}))?\s*([AaPp])?\.?[Mm]?\.?\s*$")


def normalize_time(value: Optional[str]) -> Optional[str]:
    """'8:00 AM', '8:00\u202fAM', '08:00', '8 am' -> '08:00'. Returns None if unparseable."""
    if not value:
        return None
    text = value.replace("\u202f", " ").replace("\u00a0", " ").strip()
    match = _TIME_RE.match(text)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = (match.group(3) or "").lower()
    if meridiem == "p" and hour < 12:
        hour += 12
    elif meridiem == "a" and hour == 12:
        hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return f"{hour:02d}:{minute:02d}"
