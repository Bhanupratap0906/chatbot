"""Run:  python -m scripts.check_tts        (from the project root)

Checks that Deepgram accepts container="none" + sample_rate, and times
how long the first audio byte takes.
"""

import time

from modules.tts.tts_service import TtsService


def main():
    tts = TtsService()

    start = time.perf_counter()
    first = None
    total_bytes = 0

    for chunk in tts.stream("Sure, I can help you book an appointment.", "flux-hannah-en"):
        if first is None:
            first = time.perf_counter() - start
            print(f"first audio chunk after {first:.2f}s")
            print("starts with RIFF header:", chunk[:4] == b"RIFF")

        total_bytes += len(chunk)

    elapsed = time.perf_counter() - start

    print(f"{total_bytes} bytes in {elapsed:.2f}s")

    # 16-bit mono: 2 bytes per sample
    seconds_of_audio = total_bytes / 2 / 24000
    print(f"that is {seconds_of_audio:.2f}s of speech at 24 kHz")


if __name__ == "__main__":
    main()