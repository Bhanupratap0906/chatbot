import os

from dotenv import load_dotenv
from deepgram import DeepgramClient

from modules.llm.llm_service import LLMresponse


load_dotenv()


class SttService:
    """Batch/file transcription. The realtime path lives in modules/realtime."""

    def __init__(self):
        api_key = os.getenv("DEEPGRAM_API_KEY")

        if not api_key:
            raise ValueError("DEEPGRAM_API_KEY is not configured")

        self.client = DeepgramClient(api_key=api_key)
        self.llm_service = LLMresponse()

    async def speech_to_text(self, audio: bytes):
        response = self.client.listen.v1.media.transcribe_file(
            request=audio,
            model="nova-3",
            smart_format=True,
            language="en",
        )

        transcript = response.results.channels[0].alternatives[0].transcript

        if not transcript.strip():
            return {"transcript": "", "answer": ""}

        answer = await self.llm_service.llm_response(transcript)
        return {"transcript": transcript, "answer": answer}