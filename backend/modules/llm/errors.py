"""LLM errors classification (SRP)."""

from __future__ import annotations


def is_tool_validation_error(error: Exception) -> bool:
    """
    True when the provider rejected the model's tool call server-side
    (e.g. Groq 400 tool_use_failed for empty/mistyped arguments).

    This is the model misbehaving, not a backend bug — so the turn should
    fall back to a normal reply instead of surfacing "AI error" to the caller.
    """
    if getattr(error, "status_code", None) == 400:
        return True
    message = str(error).lower()
    return "tool_use_failed" in message or "tool call validation failed" in message
