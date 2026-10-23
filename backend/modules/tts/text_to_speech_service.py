"""TTS facade (SRP + DIP + ISP + LSP + OCP).

* ``TextToSpeechService`` is a thin facade: validates, then delegates to
  ``AudioGenerator`` (blocking) and ``AsyncSpeaker`` (realtime).
* Depends on ``VoiceValidator`` / ``AudioGenerator`` ABCs (DIP), so fakes
  can be substituted in tests (LSP) and new providers added without
  editing callers (OCP).
* Implements the segregated ``SpeechSynthesizer`` interface (ISP).
* Voice constants are defined in ``modules.tts.voice_catalog`` and imported by
  callers that need them.
"""

from __future__ import annotations

from modules.common.conversation_cost_tracker import ConversationCostTracker
from modules.tts.deepgram_audio_generator import DeepgramAudioGenerator
from modules.tts.threaded_async_speaker import ThreadedTextToSpeechSpeaker
from modules.tts.voice_catalog import AllowedVoiceValidator


class TextToSpeechService:
    """Validate voice requests and coordinate audio generation for live responses."""

    def __init__(
        self,
        validator: AllowedVoiceValidator | None = None,
        generator: DeepgramAudioGenerator | None = None,
        speaker: ThreadedTextToSpeechSpeaker | None = None,
        api_key: str | None = None,
        cost_tracker: ConversationCostTracker | None = None,
    ):
        """Build the validator, Deepgram generator, and async audio speaker."""
        self.validator = validator or AllowedVoiceValidator()
        self.generator = generator or DeepgramAudioGenerator(
            api_key=api_key, cost_tracker=cost_tracker
        )
        self.speaker = speaker or ThreadedTextToSpeechSpeaker(self.generator.generate_audio_chunks)

    # ---------- SRP: validation only ----------

    def _validate_text_and_voice_for_synthesis(self, text: str, voice: str) -> None:
        """Reject empty text or unsupported voices before synthesis begins."""
        if not text or not text.strip():
            raise ValueError("Text is required")
        self.validator.validate(voice)

    async def speak(self, text: str, voice: str, send_bytes) -> None:
        """Stream one chunk of TTS audio via an async ``send_bytes`` callback."""
        self._validate_text_and_voice_for_synthesis(text, voice)
        await self.speaker.speak(text, voice, send_bytes)
