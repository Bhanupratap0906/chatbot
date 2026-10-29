"""Shared session coordinator for two-agent voice architecture.

Manages Speaking Agent (patient conversation) and Booking Agent
(appointment operations) through a session-level state machine.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Dict, Any


class BookingState(str, Enum):
    """Booking operation state machine values."""
    IDLE = "idle"
    BOOKING_IN_PROGRESS = "booking_in_progress"
    AWAITING_PATIENT_CONFIRMATION = "awaiting_patient_confirmation"
    BOOKING_SUCCEEDED = "booking_succeeded"
    BOOKING_FAILED = "booking_failed"
    CANCELLED = "cancelled"


@dataclass
class BookingResult:
    operation_id: str
    status: str
    message: str = ""
    appointment_details: Optional[Dict[str, Any]] = None
    error_code: Optional[str] = None
    requires_patient_input: bool = False
    is_terminal: bool = False


@dataclass
class SessionCoordinatorState:
    conversation_id: str = ""
    current_booking_operation_id: Optional[str] = None
    booking_status: str = BookingState.IDLE
    pending_appointment_details: Optional[Dict[str, Any]] = None
    confirmation_status: Optional[bool] = None
    latest_patient_utterance: str = ""
    latest_booking_result: Optional[BookingResult] = None
    progress_acknowledged: bool = False
    cancellation_status: bool = False
    booking_task: Optional[asyncio.Task] = None
    watchdog_task: Optional[asyncio.Task] = None


class ConversationCoordinator:
    """Session-level coordinator for Speaking Agent and Booking Agent."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.state = SessionCoordinatorState(conversation_id=session_id)
        self._lock = asyncio.Lock()
        self._booking_lock: Optional[asyncio.Lock] = None

    async def start_booking(self, details: Dict[str, Any]) -> str:
        """Initiate a new booking operation asynchronously."""
        async with self._lock:
            if self.state.booking_status == BookingState.BOOKING_IN_PROGRESS:
                # Already running; return existing operation ID
                return self.state.current_booking_operation_id or ""
            operation_id = str(uuid.uuid4())
            self.state.current_booking_operation_id = operation_id
            self.state.booking_status = BookingState.BOOKING_IN_PROGRESS
            self.state.pending_appointment_details = details
            self.state.confirmation_status = None
            self.state.progress_acknowledged = False
            self.state.cancellation_status = False
            return operation_id

    async def receive_utterance(self, text: str) -> None:
        """Record patient utterance for routing without restarting booking."""
        self.state.latest_patient_utterance = (text or "").strip()
        # Intent-aware: only treat as interruption if genuinely unrelated
        # and booking is not awaiting confirmation
        words = text.lower().split() if text else []
        backchannels = {"yeah", "yes", "okay", "ok", "uh-huh", "mm", "hmm", "right", "yep"}
        is_backchannel = (
            len(words) <= 2 and all(w.strip(".,!?") in backchannels for w in words)
        )
        if is_backchannel and self.state.booking_status == BookingState.AWAITING_PATIENT_CONFIRMATION:
            # Treat short affirmative as confirmation attempt
            affirmative = bool(__import__("re").search(
                r"\b(yes|yeah|yep|correct|right|sure|go ahead|confirm)\b",
                text.lower(),
            ))
            self.state.confirmation_status = affirmative
            self.state.progress_acknowledged = True

    async def confirm_pending_booking(self) -> bool:
        """Route confirmation to pending booking operation."""
        async with self._lock:
            if self.state.booking_status != BookingState.AWAITING_PATIENT_CONFIRMATION:
                return False
            # Require affirmative confirmation
            affirmative_words = {"yes", "yeah", "yep", "correct", "right", "sure", "go ahead", "confirm"}
            words = self.state.latest_patient_utterance.lower().split()
            is_affirmative = any(w.strip(".,!?") in affirmative_words for w in words)
            if is_affirmative:
                self.state.confirmation_status = True
                self.state.progress_acknowledged = True
                return True
            else:
                self.state.confirmation_status = False
                return False

    async def cancel_pending_booking(self) -> bool:
        """Cancel pending booking operation safely."""
        async with self._lock:
            if self.state.booking_task and not self.state.booking_task.done():
                self.state.booking_task.cancel()
                try:
                    await self.state.booking_task
                except asyncio.CancelledError:
                    pass
            self.state.booking_task = None
            if self.state.booking_status in (
                BookingState.BOOKING_IN_PROGRESS,
                BookingState.AWAITING_PATIENT_CONFIRMATION,
            ):
                self.state.booking_status = BookingState.CANCELLED
                self.state.cancellation_status = True
                return True
            return False

    async def deliver_result(self, result: BookingResult) -> None:
        """Deliver structured booking result from coordinator."""
        async with self._lock:
            if result.operation_id != self.state.current_booking_operation_id:
                # Stale event; ignore
                return
            self.state.latest_booking_result = result
            if result.status == "succeeded" or result.status == BookingState.BOOKING_SUCCEEDED:
                self.state.booking_status = BookingState.BOOKING_SUCCEEDED
            elif result.status == "failed" or result.status == BookingState.BOOKING_FAILED:
                self.state.booking_status = BookingState.BOOKING_FAILED
            elif result.status == "cancelled" or result.status == BookingState.CANCELLED:
                self.state.booking_status = BookingState.CANCELLED
            self.state.progress_acknowledged = True

    async def get_status_for_speech(self) -> str:
        """Return a brief spoken update about booking progress."""
        async with self._lock:
            if not self.state.progress_acknowledged and self.state.booking_status == BookingState.BOOKING_IN_PROGRESS:
                return "Your appointment is still being processed. I'll confirm as soon as I receive the result."
            if self.state.booking_status == BookingState.BOOKING_SUCCEEDED and self.state.latest_booking_result:
                msg = self.state.latest_booking_result.message or "Your appointment has been booked."
                return msg
            if self.state.booking_status == BookingState.BOOKING_FAILED and self.state.latest_booking_result:
                msg = self.state.latest_booking_result.message or "The booking could not be completed."
                return msg
            if self.state.booking_status == BookingState.CANCELLED:
                return "The booking request was cancelled."
            return ""
