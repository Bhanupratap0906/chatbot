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
    # backend/.env regardless of CWD (same guarantee main.py had).
    base = Path(__file__).resolve().parents[2]
    load_dotenv(base / ".env")
    load_dotenv()  # also honour CWD /.env for local overrides


_load_env_once()


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    """All tunable knobs for the voice pipeline."""

    deepgram_api_key: str = field(default_factory=lambda: os.getenv("DEEPGRAM_API_KEY", ""))
    groq_api_key: str = field(default_factory=lambda: os.getenv("GROQ_API_KEY", ""))
    groq_model: str = field(default_factory=lambda: os.getenv("GROQ_MODEL", "openai/gpt-oss-20b"))

    input_sample_rate: int = field(default_factory=lambda: int(os.getenv("INPUT_SAMPLE_RATE", "16000")))
    tts_sample_rate: int = field(default_factory=lambda: int(os.getenv("TTS_SAMPLE_RATE", "24000")))

    barge_in: bool = field(default_factory=lambda: _as_bool(os.getenv("BARGE_IN"), False))
    llm_timeout: float = field(default_factory=lambda: float(os.getenv("LLM_TIMEOUT", "20")))

    default_voice: str = field(default_factory=lambda: os.getenv("DEFAULT_VOICE", "flux-hannah-en"))
    greeting_text: str = field(
        default_factory=lambda: os.getenv(
            "GREETING_TEXT",
            "Hi, thanks for calling our dental clinic. "
            "I can help with appointments or questions about the clinic. "
            "How can I help you today?",
        )
    )
    history_window: int = field(default_factory=lambda: int(os.getenv("HISTORY_WINDOW", "12")))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached accessor for FastAPI ``Depends`` and factories."""
    return Settings()
