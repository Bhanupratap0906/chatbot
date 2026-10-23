"""Per-conversation provider usage and estimated cost reporting."""

from __future__ import annotations

import logging
from collections.abc import Mapping

from modules.common.settings import Settings

logger = logging.getLogger("cost")


class ConversationCostTracker:
    """Accumulate billable usage for one voice session and print cost estimates."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self.stt_audio_seconds = 0.0
        self.tts_characters = 0
        self.llm_input_tokens = 0
        self.llm_cached_input_tokens = 0
        self.llm_output_tokens = 0
        self.llm_cost = 0.0
        self._tts_cost = 0.0
        self._llm_requests = 0

    def add_stt_audio(self, byte_count: int, sample_rate: int) -> None:
        """Count sent mono linear16 audio; Deepgram bills streaming audio by duration."""
        self.stt_audio_seconds += byte_count / (sample_rate * 2)

    def add_tts_text(self, text: str, model: str) -> None:
        """Count text submitted to synthesis and report this request's estimated price."""
        characters = len(text)
        cost = characters * self._settings.deepgram_tts_price_per_thousand_chars / 1000
        self.tts_characters += characters
        self._tts_cost += cost
        logger.info(
            "TTS request model=%s chars=%d estimated=$%.8f", model, characters, cost
        )

    def add_llm_usage(self, usage: Mapping, model: str) -> None:
        """Record Groq's stream usage chunk and print the cost of that model request."""
        input_tokens = int(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0)
        output_tokens = int(usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0)
        prompt_details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
        cached_tokens = int(prompt_details.get("cached_tokens", 0) or 0)
        cached_tokens = min(cached_tokens, input_tokens)
        uncached_tokens = input_tokens - cached_tokens

        input_cost = (
            uncached_tokens * self._settings.groq_input_price_per_million
            + cached_tokens * self._settings.groq_cached_input_price_per_million
        ) / 1_000_000
        output_cost = (
            output_tokens * self._settings.groq_output_price_per_million / 1_000_000
        )
        request_cost = input_cost + output_cost

        self._llm_requests += 1
        self.llm_input_tokens += input_tokens
        self.llm_cached_input_tokens += cached_tokens
        self.llm_output_tokens += output_tokens
        self.llm_cost += request_cost
        logger.info(
            "LLM request model=%s input_tokens=%d (cached=%d) output_tokens=%d estimated=$%.8f",
            model, input_tokens, cached_tokens, output_tokens, request_cost,
        )

    def print_summary(self) -> None:
        """Print the accumulated speech and model charges when the voice session ends."""
        stt_minutes = self.stt_audio_seconds / 60
        stt_cost = stt_minutes * self._settings.deepgram_stt_price_per_minute
        total = stt_cost + self._tts_cost + self.llm_cost
        logger.info(
            "conversation summary STT=%.2fs/$%.8f TTS=%d chars/$%.8f "
            "LLM=%d requests, input=%d tokens (cached=%d), output=%d tokens/$%.8f TOTAL=$%.8f",
            self.stt_audio_seconds, stt_cost,
            self.tts_characters, self._tts_cost,
            self._llm_requests, self.llm_input_tokens, self.llm_cached_input_tokens,
            self.llm_output_tokens, self.llm_cost, total,
        )
