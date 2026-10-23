"""Sentence-aware chunking for streaming TTS (SRP + OCP).

``SentenceChunkSplitter`` owns all tuning (first-chunk fast path vs later
bigger chunks). To change chunking policy, add a new ``ChunkSplitter``
instead of editing the LLM/turn code (OCP).
"""

from __future__ import annotations

import re

from modules.common.backend_contracts import ChunkSplitter


SENTENCE_END = re.compile(r"[\.\!\?](?=\s|$)")

FIRST_CHUNK_CHARS = 36
LATER_CHUNK_CHARS = 160


class SentenceChunkSplitter(ChunkSplitter):
    """Default policy: first phrase fast, then bigger sentence chunks."""

    def __init__(self, first_chars: int = FIRST_CHUNK_CHARS, later_chars: int = LATER_CHUNK_CHARS):
        """Configure chunk thresholds used when splitting streamed LLM output."""
        self.first_chars = first_chars
        self.later_chars = later_chars

    def split(self, buffer: str, is_first: bool) -> tuple[str, str]:
        """Return speakable text and the remainder; called for every LLM text chunk."""
        limit = self.first_chars if is_first else self.later_chars
        matches = list(SENTENCE_END.finditer(buffer))
        if matches:
            cut = matches[-1].end()
            return buffer[:cut].strip(), buffer[cut:]
        if len(buffer) >= limit:
            cut = buffer.rfind(" ", 0, limit)
            cut = cut if cut > 0 else limit
            return buffer[:cut].strip(), buffer[cut:]
        return "", buffer
