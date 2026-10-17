"""Abstractions the whole backend depends on (DIP + ISP + LSP).

High-level modules (realtime session, controllers) depend only on these
small protocols. Low-level modules (Deepgram, Groq) implement them, so any
implementation can be substituted without changing callers.

Each protocol is deliberately tiny (ISP): clients depend only on the
method(s) they actually use.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Iterable
from typing import Any, Optional


# ---------- LLM ----------

class Guardrail(ABC):
    """SRP: decides whether a user utterance may reach the LLM."""

    @abstractmethod
    def is_allowed(self, text: str) -> bool: ...


class LlmAssistant(ABC):
    """SRP: streams a conversational reply for one user turn."""

    @abstractmethod
    def stream(self, text: str, history: Optional[list] = None) -> AsyncIterator[str]: ...


class ChunkSplitter(ABC):
    """SRP: decides which prefix of the LLM buffer is ready to speak."""

    @abstractmethod
    def split(self, buffer: str, is_first: bool) -> tuple[str, str]: ...


# ---------- STT ----------

class FileTranscriber(ABC):
    """SRP: batch transcription only (no LLM, no HTTP)."""

    @abstractmethod
    async def transcribe(self, audio: bytes) -> str: ...


class RealtimeConnector(ABC):
    """SRP: opens a realtime transcription socket."""

    @abstractmethod
    async def connect(self) -> Any: ...


# ---------- TTS (segregated so callers don't depend on what they don't use) ----------

class VoiceValidator(ABC):
    @abstractmethod
    def validate(self, voice: str) -> None: ...


class AudioGenerator(ABC):
    """SRP: blocking Deepgram chunk generation."""

    @abstractmethod
    def generate(self, text: str, voice: str, raw: bool) -> Iterable[bytes]: ...


class ChunkStreamer(ABC):
    """ISP slice: synchronous PCM chunk iteration."""

    @abstractmethod
    def stream(self, text: str, voice: str) -> Iterable[bytes]: ...


class FullSynthesizer(ABC):
    """ISP slice: whole-file synthesis for the REST route."""

    @abstractmethod
    def text_to_speech(self, text: str, voice: str) -> bytes: ...


class AsyncSpeaker(ABC):
    """ISP slice: realtime playback via a send callback."""

    @abstractmethod
    async def speak(self, text: str, voice: str, send_bytes) -> None: ...


class SpeechSynthesizer(ChunkStreamer, FullSynthesizer, AsyncSpeaker, ABC):
    """Facade interface; concrete TtsService implements all three slices."""
    ...


# ---------- Tools / appointments ----------

class AppointmentRepository(ABC):
    """SRP: persistence only. Business rules live in the validator/use-case."""

    @abstractmethod
    async def book(self, patient_name: str, appointment_date: str, appointment_time: str) -> str: ...


class AppointmentValidator(ABC):
    @abstractmethod
    def missing_fields(self, patient_name: Optional[str], appointment_date: Optional[str],
                        appointment_time: Optional[str]) -> list[str]: ...


# ---------- Realtime session ----------

class MessagePublisher(ABC):
    """ISP slice: what a turn needs from a websocket (send only)."""

    @abstractmethod
    async def send_json(self, payload: dict) -> None: ...

    @abstractmethod
    async def send_bytes(self, data: bytes) -> None: ...


class ConversationHistory(ABC):
    @abstractmethod
    def snapshot(self) -> list: ...

    @abstractmethod
    def add_turn(self, user_text: str, ai_text: str) -> None: ...

    @abstractmethod
    def add_ai(self, text: str) -> None: ...

