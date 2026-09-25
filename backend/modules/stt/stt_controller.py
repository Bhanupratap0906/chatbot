"""STT REST route — thin adapter (SRP + DIP).

Depends on ``FileTranscriber`` + ``LlmAssistant`` ABCs via ``Depends``.
No global service instances, no concrete imports at call time (DIP):
swap providers in ``dependencies.py`` without touching this file (OCP).
Composition (STT + optional LLM answer) lives here explicitly instead
of being coupled to the transcription provider (SRP).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile

from modules.common.dependencies import get_file_transcriber, get_llm
from modules.common.protocols import FileTranscriber, LlmAssistant


router = APIRouter(prefix="/stt", tags=["STT"])


@router.post("/audio")
async def speech_to_text(
    audio: UploadFile = File(...),
    transcriber: FileTranscriber = Depends(get_file_transcriber),
    llm: LlmAssistant = Depends(get_llm),
):
    audio_data = await audio.read()
    transcript = await transcriber.transcribe(audio_data)

    if not transcript.strip():
        return {"success": True, "transcript": "", "answer": ""}

    # Compose transcription and the optional LLM response in this route.
    try:
        answer = await llm.llm_response(transcript)
    except Exception:
        answer = ""

    return {"success": True, "transcript": transcript, "answer": answer}
