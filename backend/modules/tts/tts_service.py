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

from collections.abc import Iterable

from modules.common.protocols import SpeechSynthesizer
from modules.common.settings import get_settings
from modules.tts.generator import DeepgramAudioGenerator
from modules.tts.speaker import ThreadedAsyncSpeaker
from modules.tts.voices import AllowedVoiceValidator


class TtsService(SpeechSynthesizer):
    def __init__(
        self,
        validator: AllowedVoiceValidator | None = None,
        generator: DeepgramAudioGenerator | None = None,
        speaker: ThreadedAsyncSpeaker | None = None,
        api_key: str | None = None,
    ):
        settings = get_settings()
        resolved_key = api_key if api_key is not None else settings.deepgram_api_key
        if not resolved_key and generator is None:
            raise ValueError("DEEPGRAM_API_KEY is not configured")

        self.validator = validator or AllowedVoiceValidator()
        self.generator = generator or DeepgramAudioGenerator()
        # DIP: speaker streams via the facade's own ``stream`` so validation
        # stays in one place; generator remains swappable underneath.
        self.speaker = speaker or ThreadedAsyncSpeaker(self.validator, self.stream)

    # ---------- SRP: validation only ----------

    def _validate(self, text: str, voice: str) -> None:
        if not text or not text.strip():
            raise ValueError("Text is required")
        self.validator.validate(voice)

    def _generate(self, text: str, voice: str, raw: bool):
        return self.generator.generate(text, voice, raw)

    # ---------- SpeechSynthesizer interface ----------

    def stream(self, text: str, voice: str) -> Iterable[bytes]:
        """Blocking generator of linear16 chunks (WAV fallback inside)."""
        self._validate(text, voice)
        yield from self.generator.iter_chunks(text, voice)

    def text_to_speech(self, text: str, voice: str) -> bytes:
        """Full WAV, kept for the old REST route."""
        self._validate(text, voice)
        audio = b"".join(chunk for chunk in self._generate(text, voice, raw=False) if chunk)
        if not audio:
            raise RuntimeError("Deepgram returned no audio data")
        return audio

    async def speak(self, text: str, voice: str, send_bytes) -> None:
        """Stream one chunk of TTS audio via an async ``send_bytes`` callback."""
        self._validate(text, voice)
        await self.speaker.speak(text, voice, send_bytes)
