"""Voice catalogue + validation (SRP)."""

from __future__ import annotations

from modules.common.protocols import VoiceValidator


VOICES = [
    "flux-hannah-en", "flux-kit-en", "flux-alexis-en", "flux-cliff-en",
    "flux-sienna-en", "flux-cole-en", "flux-brooke-en", "flux-colin-en",
    "flux-gemma-en", "flux-haley-en", "flux-heather-en", "flux-miles-en",
    "flux-sean-en", "flux-bree-en", "flux-brittany-en", "flux-bruce-en",
    "flux-conor-en", "flux-donovan-en", "flux-drew-en", "flux-elise-en",
    "flux-jack-en", "flux-kai-en", "flux-kelsey-en", "flux-maeve-en",
    "flux-marcelo-en", "flux-marcus-en", "flux-meena-en", "flux-meghan-en",
    "flux-naveen-en", "flux-paige-en", "flux-priya-en", "flux-rufus-en",
    "flux-sharon-en", "flux-tanner-en", "flux-wade-en", "flux-wes-en",
]

SAMPLE_RATE = 24000


class AllowedVoiceValidator(VoiceValidator):
    """SRP: only knows which voice names are allowed."""

    def __init__(self, voices: list[str] | None = None):
        self._voices = list(voices) if voices is not None else list(VOICES)

    @property
    def voices(self) -> list[str]:
        return list(self._voices)

    def validate(self, voice: str) -> None:
        if voice not in self._voices:
            raise ValueError(f"Invalid voice: {voice}")
