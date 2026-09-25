"""TTS REST route — thin adapter (SRP + DIP).

Depends on the ``SpeechSynthesizer`` interface via ``Depends`` instead of a
module-global ``TtsService()`` (DIP). Any implementation can be substituted.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from modules.common.dependencies import get_tts


router = APIRouter(prefix="/tts", tags=["TTS"])


@router.get("/audio")
async def generate_audio(
    text: str = Query(..., description="Text to convert into speech",
                      examples=["Hello, how can I help you today?"]),
    voiceName: str = Query(..., description="Deepgram voice model",
                           examples=["flux-hannah-en"]),
    tts=Depends(get_tts),
):
    audio = tts.text_to_speech(text=text, voice=voiceName)
    return Response(
        content=audio,
        media_type="audio/wav",
        headers={
            "Content-Length": str(len(audio)),
            "Content-Disposition": 'inline; filename="speech.wav"',
        },
    )
