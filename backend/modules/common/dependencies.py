"""FastAPI dependency providers (DIP wiring).

Controllers declare *interfaces* via ``Depends``; these factories decide
which concrete class to build. Swapping providers (tests, new vendors)
never touches controller code (OCP).
"""

from __future__ import annotations

from functools import lru_cache

from modules.common.protocols import (
    FileTranscriber,
    LlmAssistant,
    SpeechSynthesizer,
)


@lru_cache(maxsize=1)
def _cached_tts() -> SpeechSynthesizer:
    from modules.tts.tts_service import TtsService

    return TtsService()


@lru_cache(maxsize=1)
def _cached_llm() -> LlmAssistant:
    from modules.llm.llm_service import LLMresponse

    return LLMresponse()


def get_tts() -> SpeechSynthesizer:
    return _cached_tts()


def get_llm() -> LlmAssistant:
    return _cached_llm()


def get_file_transcriber() -> FileTranscriber:
    from modules.stt.stt_service import DeepgramFileTranscriber

    return DeepgramFileTranscriber()

