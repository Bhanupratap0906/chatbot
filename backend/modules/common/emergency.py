"""Deterministic emergency triage (SRP)."""

from __future__ import annotations

import re

# Each pattern is a (symptom_regex, instruction) pair.
# Detection is clause-aware: split on commas, semicolons, and "but"/"and" clauses,
# and ignore a clause that explicitly negates the symptom.
SYMPTOM_PATTERNS: list[tuple[str, str]] = [
    (r"swell(?:ing|en)?", "swelling/swollen"),
    (r"fever|hot|temperature", "fever"),
    (r"trouble|difficulty|hard", "difficulty"),
    (r"breath(?:ing|e)?", "breathing"),
    (r"swallow(?:ing)?", "swallowing"),
    (r"bleed(?:ing)?", "bleeding"),
    (r"uncontrolled|won'?t stop|heavy", "uncontrolled/heavy"),
]

NEGATION_WORDS = {"no", "not", "without", "never", "nothing", "none", "n't", "don't", "doesn't", "didn't", "wasn't", "weren't", "isn't", "aren't", "haven't", "hasn't", "hadn't", "wouldn't", "shouldn't", "couldn't", "can't", "cannot"}


def _has_negation_in_clause(clause: str) -> bool:
    words = re.findall(r"\b\w+\b", clause.lower())
    # Check multi-word negations first
    lowered = clause.lower()
    for phrase in ("no trouble", "not right", "not correct", "no fever", "no swelling", "no bleeding", "not sure", "nothing like", "don't have", "doesn't have", "didn't have", "don't have any", "doesn't have any"):
        if phrase in lowered:
            return True
    # Treat "can't swallow" / "can't breathe" as symptoms, not negations
    symptom_words = {"swallow", "swallowing", "breathe", "breathing", "swelling", "fever", "bleeding", "bleed"}
    for i, w in enumerate(words):
        w_stripped = w.strip(".,;:!?")
        if w_stripped.startswith("n'") or w_stripped.startswith("n'"):
            # "can't" followed by a symptom is an emergency
            if i + 1 < len(words) and words[i + 1].strip(".,;:!?") in symptom_words:
                continue
            # "can't" without symptom is a negation (e.g. "can't confirm")
            if w_stripped in ("can't", "cant", "cannot"):
                return True
        if w_stripped in NEGATION_WORDS:
            # If the next word is a symptom, this is an emergency statement
            if i + 1 < len(words) and words[i + 1].strip(".,;:!?") in symptom_words:
                continue
            return True
    return False


def is_emergency(text: str) -> bool:
    if not text:
        return False
    lowered = text.lower()
    # Split into clauses on punctuation and conjunctions
    clauses = re.split(r"[,;]\s*|\s+but\s+|\s+and\s+", lowered)
    for clause in clauses:
        if not clause.strip():
            continue
        # If the clause is explicitly negated, skip it.
        if _has_negation_in_clause(clause):
            continue
        # Check for symptom presence in non-negated clauses.
        has_symptom = False
        # Swelling + fever
        if ("swelling" in clause or "swollen" in clause) and ("fever" in clause or "hot" in clause or "temperature" in clause):
            has_symptom = True
        elif ("fever" in clause or "hot" in clause or "temperature" in clause) and ("swelling" in clause or "swollen" in clause):
            has_symptom = True
        # Breathing/swallowing trouble
        elif ("trouble" in clause or "difficulty" in clause or "hard" in clause) and ("breathing" in clause or "breath" in clause or "swallow" in clause or "swallowing" in clause):
            has_symptom = True
        elif ("breathing" in clause or "breath" in clause or "swallow" in clause or "swallowing" in clause) and ("trouble" in clause or "difficulty" in clause or "hard" in clause):
            has_symptom = True
        # Bleeding
        elif ("bleeding" in clause or "bleed" in clause) and ("uncontrolled" in clause or "won't stop" in clause or "wont stop" in clause or "heavy" in clause):
            has_symptom = True
        elif ("uncontrolled" in clause or "won't stop" in clause or "wont stop" in clause or "heavy" in clause) and ("bleeding" in clause or "bleed" in clause):
            has_symptom = True
        # Individual symptoms that are unambiguous
        elif "swelling" in clause or "swollen" in clause:
            has_symptom = True
        elif "fever" in clause or "temperature" in clause or ("hot" in clause and "swelling" in clause):
            has_symptom = True
        elif "trouble breathing" in clause or "difficulty breathing" in clause or "hard breathing" in clause:
            has_symptom = True
        elif "trouble swallowing" in clause or "difficulty swallowing" in clause or "hard swallowing" in clause or ("can't" in clause and ("swallow" in clause or "swallowing" in clause)):
            has_symptom = True
        elif "bleeding" in clause or ("bleed" in clause and ("won't" in clause or "wont" in clause or "uncontrolled" in clause or "heavy" in clause)):
            has_symptom = True
        if has_symptom:
            return True
    return False


def emergency_instruction(text: str) -> str:
    return "That sounds like a dental emergency. Please seek urgent care or go to the ER now."
