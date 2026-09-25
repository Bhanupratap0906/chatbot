"""Realtime Deepgram connector (SRP + DIP).

Only opens the ``/v1/listen`` websocket. URL building, headers and the
``websockets<14`` fallback live here so the realtime session doesn't
need provider details (SRP). Session depends on ``RealtimeConnector``
ABC (DIP); tests inject a fake (LSP).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from modules.common.protocols import RealtimeConnector
from modules.common.settings import get_settings


def build_listen_url(input_sample_rate: int) -> str:
    return "wss://api.deepgram.com/v1/listen?" + urlencode(
        {
            "model": "nova-3",
            "language": "en",
            "encoding": "linear16",
            "sample_rate": input_sample_rate,
            "channels": 1,
            "smart_format": "true",
            "punctuate": "true",
            "interim_results": "true",
            "vad_events": "true",
            "endpointing": "300",
            "utterance_end_ms": "1000",
        }
    )


class DeepgramRealtimeConnector(RealtimeConnector):
    def __init__(self, api_key: str | None = None, input_sample_rate: int | None = None):
        settings = get_settings()
        self._api_key = api_key if api_key is not None else settings.deepgram_api_key
        self._sample_rate = input_sample_rate if input_sample_rate is not None else settings.input_sample_rate

    async def connect(self) -> Any:
        import websockets

        if not self._api_key:
            raise ValueError("DEEPGRAM_API_KEY is not configured")
        url = build_listen_url(self._sample_rate)
        headers = {"Authorization": f"Token {self._api_key}"}
        try:
            return await websockets.connect(url, additional_headers=headers, max_size=None)
        except TypeError:
            # websockets < 14 uses extra_headers
            return await websockets.connect(url, extra_headers=headers, max_size=None)
