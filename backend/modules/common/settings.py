"""Central application settings (SRP + DIP).

Single place that reads the environment. All services receive a
``Settings`` instance (or the primitives they need) instead of calling
``os.getenv`` / ``load_dotenv`` themselves.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv


def _load_env_once() -> None:
    """Load backend and current-directory environment files during module setup."""
    # backend/.env regardless of CWD (same guarantee main.py had).
    base = Path(__file__).resolve().parents[2]
    load_dotenv(base / ".env")
    load_dotenv()  # also honour CWD /.env for local overrides


_load_env_once()


def _as_bool(value: str | None, default: bool = False) -> bool:
    """Interpret common environment values as booleans when settings are created."""
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    """All tunable knobs for the voice pipeline."""

    deepgram_api_key: str = field(default_factory=lambda: os.getenv("DEEPGRAM_API_KEY", ""))
    groq_api_key: str = field(default_factory=lambda: os.getenv("GROQ_API_KEY", ""))
    groq_model: str = field(default_factory=lambda: os.getenv("GROQ_MODEL", "openai/gpt-oss-20b"))
    groq_input_price_per_million: float = field(
        default_factory=lambda: float(os.getenv("GROQ_INPUT_PRICE_PER_MILLION", "0.075"))
    )
    groq_cached_input_price_per_million: float = field(
        default_factory=lambda: float(os.getenv("GROQ_CACHED_INPUT_PRICE_PER_MILLION", "0.037"))
    )
    groq_output_price_per_million: float = field(
        default_factory=lambda: float(os.getenv("GROQ_OUTPUT_PRICE_PER_MILLION", "0.30"))
    )
    deepgram_stt_price_per_minute: float = field(
        default_factory=lambda: float(os.getenv("DEEPGRAM_STT_PRICE_PER_MINUTE", "0.0048"))
    )
    deepgram_tts_price_per_thousand_chars: float = field(
        default_factory=lambda: float(os.getenv("DEEPGRAM_TTS_PRICE_PER_1000_CHARS", "0.045"))
    )

    input_sample_rate: int = field(default_factory=lambda: int(os.getenv("INPUT_SAMPLE_RATE", "16000")))
    tts_sample_rate: int = field(default_factory=lambda: int(os.getenv("TTS_SAMPLE_RATE", "24000")))

    barge_in: bool = field(default_factory=lambda: _as_bool(os.getenv("BARGE_IN"), False))
    llm_timeout: float = field(default_factory=lambda: float(os.getenv("LLM_TIMEOUT", "20")))
    llm_max_tokens: int = field(default_factory=lambda: int(os.getenv("LLM_MAX_TOKENS", "160")))

    default_voice: str = field(default_factory=lambda: os.getenv("DEFAULT_VOICE", "flux-hannah-en"))
    greeting_text: str = field(
        default_factory=lambda: os.getenv(
            "GREETING_TEXT",
            "Thanks for calling our dental clinic. How can I help?",
        )
    )
    history_window: int = field(default_factory=lambda: int(os.getenv("HISTORY_WINDOW", "8")))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached accessor for FastAPI ``Depends`` and factories."""
    return Settings()
