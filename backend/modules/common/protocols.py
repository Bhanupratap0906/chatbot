"""Abstractions the whole backend depends on (DIP + ISP + LSP).

High-level modules (realtime session, controllers) depend only on these
small protocols. Low-level modules (Deepgram, Groq) implement them, so any
implementation can be substituted without changing callers.

Each protocol is deliberately tiny (ISP): clients depend only on the
method(s) they actually use.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any, Optional


# ---------- LLM ----------

class Guardrail(ABC):
    """SRP: decides whether a user utterance may reach the LLM."""

    @abstractmethod
    def is_allowed(self, text: str) -> bool:
        """Return whether a caller message may be sent to the LLM."""


class LlmAssistant(ABC):
    """SRP: streams a conversational reply for one user turn."""

    @abstractmethod
    def stream(self, text: str, history: Optional[list] = None) -> AsyncIterator[str]:
        """Yield a response for one user message and its conversation history."""


class ChunkSplitter(ABC):
    """SRP: decides which prefix of the LLM buffer is ready to speak."""

    @abstractmethod
    def split(self, buffer: str, is_first: bool) -> tuple[str, str]:
        """Separate speech-ready text from text that should remain buffered."""


# ---------- STT ----------

class RealtimeConnector(ABC):
    """SRP: opens a realtime transcription socket."""

    @abstractmethod
    async def connect(self) -> Any:
        """Open the provider socket used to transcribe live caller audio."""


# ---------- TTS (segregated so callers don't depend on what they don't use) ----------

class VoiceValidator(ABC):
    """Contract for checking that a requested speech voice is supported."""

    @abstractmethod
    def validate(self, voice: str) -> None:
        """Raise an error when the requested voice is not supported."""


class AsyncSpeaker(ABC):
    """ISP slice: realtime playback via a send callback."""

    @abstractmethod
    async def speak(self, text: str, voice: str, send_bytes) -> None:
        """Generate speech and deliver audio chunks through the callback."""


# ---------- Tools / appointments ----------

class AppointmentRepository(ABC):
    """SRP: persistence only. Business rules live in the validator/use-case."""

    @abstractmethod
    async def book(self, patient_name: str, appointment_date: str, appointment_time: str) -> str:
        """Persist an appointment and return a result for the assistant to report."""


class AppointmentValidator(ABC):
    """Contract for identifying missing appointment details."""

    @abstractmethod
    def missing_fields(self, patient_name: Optional[str], appointment_date: Optional[str],
                        appointment_time: Optional[str]) -> list[str]:
        """Return the required booking fields that are empty or absent."""


# ---------- Realtime session ----------

class MessagePublisher(ABC):
    """ISP slice: what a turn needs from a websocket (send only)."""

    @abstractmethod
    async def send_json(self, payload: dict) -> None:
        """Send a structured event to the connected frontend."""

    @abstractmethod
    async def send_bytes(self, data: bytes) -> None:
        """Send an audio chunk to the connected frontend."""


class ConversationHistory(ABC):
    """Contract for retaining conversation context between turns."""

    @abstractmethod
    def snapshot(self) -> list:
        """Return recent messages in the format expected by the LLM."""

    @abstractmethod
    def add_turn(self, user_text: str, ai_text: str) -> None:
        """Store a completed user/assistant exchange."""

    @abstractmethod
    def add_ai(self, text: str) -> None:
        """Store assistant text without a corresponding user message."""
