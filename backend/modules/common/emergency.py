"""Deterministic emergency triage (SRP).

Pain/swelling/breathing triage used to be a prompt instruction only. This
module provides a keyword check that runs before the LLM so an emergency
phrase always gets the scripted instruction and escalation.
"""

from __future__ import annotations

import re

# Each pattern is a (regex, instruction) pair. Patterns are matched
# case-insensitively against the caller's utterance.
EMERGENCY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"\b(swelling|swollen)\b.*\b(fever|hot|temperature)\b", re.I),
        "That sounds like a dental emergency. Please seek urgent care or go to the ER now.",
    ),
    (
        re.compile(r"\b(fever|temperature)\b.*\b(swelling|swollen)\b", re.I),
        "That sounds like a dental emergency. Please seek urgent care or go to the ER now.",
    ),
    (
        re.compile(r"\b(trouble|difficulty|hard)\b.*\b(breath|breathing|swallow)\b", re.I),
        "That sounds like a dental emergency. Please seek urgent care or go to the ER now.",
    ),
    (
        re.compile(r"\b(breath|breathing|swallow)\b.*\b(trouble|difficulty|hard)\b", re.I),
        "That sounds like a dental emergency. Please seek urgent care or go to the ER now.",
    ),
    (
        re.compile(r"\b(uncontrolled|won'?t stop|heavy)\b.*\b(bleed|bleeding)\b", re.I),
        "That sounds like a dental emergency. Please seek urgent care or go to the ER now.",
    ),
    (
        re.compile(r"\b(bleed|bleeding)\b.*\b(uncontrolled|won'?t stop|heavy)\b", re.I),
        "That sounds like a dental emergency. Please seek urgent care or go to the ER now.",
    ),
]


def is_emergency(text: str) -> bool:
    """Return True when ``text`` matches a deterministic emergency phrase."""
    if not text:
        return False
    return any(pattern.search(text) for pattern, _ in EMERGENCY_PATTERNS)


def emergency_instruction(text: str) -> str:
    """Return the scripted instruction for an emergency utterance."""
    for pattern, instruction in EMERGENCY_PATTERNS:
        if pattern.search(text):
            return instruction
    return "That sounds like a dental emergency. Please seek urgent care or go to the ER now."
