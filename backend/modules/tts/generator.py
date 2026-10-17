"""Blocking Deepgram audio generation (SRP).

``DeepgramAudioGenerator`` only talks to Deepgram. Raw-PCM probing,
WAV fallback and header handling stay here so the async bridge and the
facade don't need to know provider quirks (SRP). New providers implement
``AudioGenerator`` without touching callers (OCP/LSP).
"""

from __future__ import annotations

from collections.abc import Iterable

from deepgram import DeepgramClient

from modules.common.protocols import AudioGenerator
from modules.common.settings import get_settings


class DeepgramAudioGenerator(AudioGenerator):
    def __init__(
        self,
        client: DeepgramClient | None = None,
        sample_rate: int | None = None,
        api_key: str | None = None,
    ):
        settings = get_settings()
        resolved_key = api_key if api_key is not None else settings.deepgram_api_key
        if not resolved_key and client is None:
            raise ValueError("DEEPGRAM_API_KEY is not configured")
        self._client = client or DeepgramClient(api_key=resolved_key)
        self._sample_rate = sample_rate if sample_rate is not None else settings.tts_sample_rate
        # Set once we know which kwargs this account/SDK actually accepts.
        self._raw_pcm_supported: bool | None = None

    def _generate_raw(self, text: str, voice: str, raw: bool):
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

    def generate(self, text: str, voice: str, raw: bool) -> Iterable[bytes]:
        return self._generate_raw(text, voice, raw)

    def iter_chunks(self, text: str, voice: str) -> Iterable[bytes]:
        """Blocking generator of linear16 chunks with WAV fallback."""
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
