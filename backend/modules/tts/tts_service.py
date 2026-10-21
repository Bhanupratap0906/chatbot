"""TTS facade (SRP + DIP + ISP + LSP + OCP).

* ``TtsService`` is a thin facade: validates, then delegates to
  ``AudioGenerator`` (blocking) and ``AsyncSpeaker`` (realtime).
* Depends on ``VoiceValidator`` / ``AudioGenerator`` ABCs (DIP), so fakes
  can be substituted in tests (LSP) and new providers added without
  editing callers (OCP).
* Implements the segregated ``SpeechSynthesizer`` interface (ISP).
* Voice constants are defined in ``modules.tts.voices`` and imported by
  callers that need them.
"""

from __future__ import annotations

from modules.common.costs import ConversationCostTracker
from modules.tts.generator import DeepgramAudioGenerator
from modules.tts.speaker import ThreadedAsyncSpeaker
from modules.tts.voices import AllowedVoiceValidator


class TtsService:
    """Validate voice requests and coordinate audio generation for live responses."""

    def __init__(
        self,
        validator: AllowedVoiceValidator | None = None,
        generator: DeepgramAudioGenerator | None = None,
        speaker: ThreadedAsyncSpeaker | None = None,
        api_key: str | None = None,
        cost_tracker: ConversationCostTracker | None = None,
    ):
        """Build the validator, Deepgram generator, and async audio speaker."""
        self.validator = validator or AllowedVoiceValidator()
        self.generator = generator or DeepgramAudioGenerator(
            api_key=api_key, cost_tracker=cost_tracker
        )
        self.speaker = speaker or ThreadedAsyncSpeaker(self.generator.iter_chunks)

    # ---------- SRP: validation only ----------

    def _validate(self, text: str, voice: str) -> None:
        """Reject empty text or unsupported voices before synthesis begins."""
        if not text or not text.strip():
            raise ValueError("Text is required")
        self.validator.validate(voice)

    async def speak(self, text: str, voice: str, send_bytes) -> None:
        """Stream one chunk of TTS audio via an async ``send_bytes`` callback."""
        self._validate(text, voice)
        await self.speaker.speak(text, voice, send_bytes)
