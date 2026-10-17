"""Prompt-injection guardrail (SRP + OCP + DIP).

* ``Guardrail`` protocol lives in ``modules.common.protocols``.
* ``RegexInjectionGuardrail`` is one implementation; add new checks by
  adding a new class or new patterns list — no edits to LLM code (OCP).
* LLM depends on the ``Guardrail`` ABC, not this concrete class (DIP).
* ``LLMresponse`` uses this implementation through its ``Guardrail`` interface.
"""

from __future__ import annotations

import re

from modules.common.protocols import Guardrail


INJECTION_PATTERNS = [
    r"ignore (all |the )?(previous|prior|above) (instructions?|prompts?|rules?)",
    r"disregard (the |all )?(previous|prior|earlier)",
    r"forget (everything|your instructions?|the rules?)",
    r"you are (now |a )?(DAN|jailbroken|unrestricted|unfiltered)",
    r"pretend (you are|to be) .{0,40}(no restrictions?|uncensored)",
    r"</?(system|user|assistant|im_start|im_end)>",
    r"new (instructions?|system prompt|rules?):",
    r"reveal your (system )?prompt",
    r"what (are|were) your (original )?instructions?",
]


class RegexInjectionGuardrail(Guardrail):
    """Rule-based injection check (no LLM round-trip)."""

    def __init__(self, patterns: list[str] | None = None):
        # OCP: custom pattern sets can be injected without subclassing.
        self._patterns = list(patterns) if patterns is not None else list(INJECTION_PATTERNS)
        self._compiled = [re.compile(p, re.IGNORECASE) for p in self._patterns]

    def is_injection(self, user_message: str) -> bool:
        return any(rx.search(user_message) for rx in self._compiled)

    def is_allowed(self, text: str) -> bool:
        if self.is_injection(text):
            print("[guardrail] prompt injection detected")
            return False
        return True
