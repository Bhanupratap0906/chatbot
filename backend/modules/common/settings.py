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


def _require_env(name: str) -> str:
    """Fail fast when a required environment variable is missing."""
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


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
    llm_max_tokens: int = field(default_factory=lambda: int(os.getenv("LLM_MAX_TOKENS", "512")))
    llm_max_retries: int = field(default_factory=lambda: int(os.getenv("LLM_MAX_RETRIES", "2")))

    default_voice: str = field(default_factory=lambda: os.getenv("DEFAULT_VOICE", "flux-hannah-en"))
    greeting_text: str = field(
        default_factory=lambda: os.getenv(
            "GREETING_TEXT",
            "Thanks for calling our dental clinic. How can I help?",
        )
    )
    history_window: int = field(default_factory=lambda: int(os.getenv("HISTORY_WINDOW", "8")))
    clinic_timezone: str = field(default_factory=lambda: os.getenv("CLINIC_TIMEZONE", "Asia/Kolkata"))
    no_answer_timeout: float = field(default_factory=lambda: float(os.getenv("NO_ANSWER_TIMEOUT", "10")))

    # Customer portal (Daily) API — login page hands credentials to the
    # backend, which exchanges them for the portal JWT and mirrors it into
    # a chatbot-domain cookie (see modules/portal/). The active locationId
    # is read from the JWT session (modules/portal/session.py); the
    # PORTAL_LOCATION_ID below is only a last-resort override.
    portal_base_url: str = field(
        default_factory=lambda: os.getenv("PORTAL_BASE_URL", "http://localhost:50001/api")
    )
    portal_timeout: float = field(default_factory=lambda: float(os.getenv("PORTAL_TIMEOUT", "15")))
    portal_cookie_name: str = field(
        default_factory=lambda: os.getenv("PORTAL_COOKIE_NAME", "portal_access_token")
    )
    portal_cookie_max_age: int = field(
        default_factory=lambda: int(os.getenv("PORTAL_COOKIE_MAX_AGE", "86400"))
    )
    portal_location_id: str = field(
        default_factory=lambda: os.getenv("PORTAL_LOCATION_ID", "")
    )
    portal_location_encryption_key: str = field(
        default_factory=lambda: os.getenv("PORTAL_LOCATION_ENCRYPTION_KEY", "")
    )
    portal_appointment_duration_minutes: int = field(
        default_factory=lambda: int(os.getenv("PORTAL_APPOINTMENT_DURATION_MINUTES", "30"))
    )
    api_log_bodies: bool = field(
        default_factory=lambda: _as_bool(os.getenv("API_LOG_BODIES"), True)
    )
    cookie_secure: bool = field(
        default_factory=lambda: _as_bool(os.getenv("COOKIE_SECURE"), True)
    )
    cookie_samesite: str = field(
        default_factory=lambda: os.getenv("COOKIE_SAMESITE", "lax")
    )
    allowed_origins: list[str] = field(
        default_factory=lambda: [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",") if o.strip()]
    )
    max_call_seconds: int = field(
        default_factory=lambda: int(os.getenv("MAX_CALL_SECONDS", "600"))
    )
    max_concurrent_calls_per_ip: int = field(
        default_factory=lambda: int(os.getenv("MAX_CONCURRENT_CALLS_PER_IP", "5"))
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached accessor for FastAPI ``Depends`` and factories."""
    return Settings()
