"""Blocking Deepgram audio generation (SRP).

``DeepgramAudioGenerator`` only talks to Deepgram. Raw-PCM probing,
WAV fallback and header handling stay here so the async bridge and the
facade don't need to know provider quirks (SRP). New providers implement
``AudioGenerator`` without touching callers (OCP/LSP).
"""

from __future__ import annotations

from collections.abc import Iterable

from deepgram import DeepgramClient

from modules.common.costs import ConversationCostTracker
from modules.common.settings import get_settings


class DeepgramAudioGenerator:
    """Request speech audio from Deepgram and yield its PCM chunks."""

    def __init__(
        self,
        client: DeepgramClient | None = None,
        sample_rate: int | None = None,
        api_key: str | None = None,
        cost_tracker: ConversationCostTracker | None = None,
    ):
        """Configure the Deepgram client and output sample rate."""
        settings = get_settings()
        resolved_key = api_key if api_key is not None else settings.deepgram_api_key
        if not resolved_key and client is None:
            raise ValueError("DEEPGRAM_API_KEY is not configured")
        self._client = client or DeepgramClient(api_key=resolved_key)
        self._sample_rate = sample_rate if sample_rate is not None else settings.tts_sample_rate
        self._cost_tracker = cost_tracker
        # Set once we know which kwargs this account/SDK actually accepts.
        self._raw_pcm_supported: bool | None = None

    def _generate_raw(self, text: str, voice: str, raw: bool):
        """Request either raw PCM or WAV fallback audio from Deepgram."""
        if raw:
            return self._client.speak.v2.audio.generate(
                text=text,
                model=voice,
                encoding="linear16",
                sample_rate=self._sample_rate,
                container="none",
            )
        return self._client.speak.v2.audio.generate(
            text=text,
            model=voice,
            encoding="linear16",
            container="wav",
        )

    def iter_chunks(self, text: str, voice: str) -> Iterable[bytes]:
        """Blocking generator of linear16 chunks with WAV fallback."""
        if self._cost_tracker is not None:
            self._cost_tracker.add_tts_text(text, voice)
        if self._raw_pcm_supported is None:
            try:
                response = self._generate_raw(text, voice, raw=True)
                iterator = iter(response)
                first = next(iterator)
                self._raw_pcm_supported = True
                if first:
                    yield first
                for chunk in iterator:
                    if chunk:
                        yield chunk
                return
            except StopIteration:
                self._raw_pcm_supported = True
                return
            except Exception as error:
                print("Deepgram rejected raw PCM, falling back to WAV:", error)
                self._raw_pcm_supported = False

        for chunk in self._generate_raw(text, voice, raw=self._raw_pcm_supported):
            if chunk:
                yield chunk
