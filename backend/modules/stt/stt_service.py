"""File transcription implementation and input rate used by realtime STT."""

from __future__ import annotations

from deepgram import DeepgramClient

from modules.common.protocols import FileTranscriber
from modules.common.settings import get_settings
# Must match INPUT_SAMPLE_RATE in the frontend, which forces its
# AudioContext to this rate before sending PCM here.
def _default_input_rate() -> int:
    try:
        return get_settings().input_sample_rate
    except Exception:
        return 16000


INPUT_SAMPLE_RATE = _default_input_rate()


class DeepgramFileTranscriber(FileTranscriber):
    """SRP: single Deepgram ``transcribe_file`` call."""

    def __init__(self, client: DeepgramClient | None = None, api_key: str | None = None):
        resolved = api_key if api_key is not None else get_settings().deepgram_api_key
        if not resolved and client is None:
            raise ValueError("DEEPGRAM_API_KEY is not configured")
        self.client = client or DeepgramClient(api_key=resolved)

    async def transcribe(self, audio: bytes) -> str:
        response = self.client.listen.v1.media.transcribe_file(
            request=audio,
            model="nova-3",
            smart_format=True,
            language="en",
        )
        return response.results.channels[0].alternatives[0].transcript


__all__ = [
    "INPUT_SAMPLE_RATE",
    "DeepgramFileTranscriber",
]
