"""Run:  python -m scripts.check_tts        (from the project root)

Checks that Deepgram accepts container="none" + sample_rate, and times
how long the first audio byte takes.
"""

import time
import asyncio

from modules.tts.tts_service import TtsService
from modules.common.settings import get_settings


async def main():
    """Smoke-test TTS streaming and report first-chunk latency; run manually when checking Deepgram TTS."""
    tts = TtsService()

    start = time.perf_counter()
    first = None
    total_bytes = 0

    async def count_chunk(chunk: bytes):
        """Record audio size and first-chunk latency for the smoke test."""
        nonlocal first, total_bytes
        if first is None:
            first = time.perf_counter() - start
            print(f"first audio chunk after {first:.2f}s")
            print("starts with RIFF header:", chunk[:4] == b"RIFF")

        total_bytes += len(chunk)

    await tts.speak("Sure, I can help you book an appointment.", "flux-hannah-en", count_chunk)

    elapsed = time.perf_counter() - start

    print(f"{total_bytes} bytes in {elapsed:.2f}s")

    # 16-bit mono: 2 bytes per sample
    seconds_of_audio = total_bytes / 2 / get_settings().tts_sample_rate
    print(f"that is {seconds_of_audio:.2f}s of speech at {get_settings().tts_sample_rate} Hz")


if __name__ == "__main__":
    asyncio.run(main())
