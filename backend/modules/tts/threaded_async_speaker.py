"""Async bridge for blocking TTS generation (SRP).

``ThreadedTextToSpeechSpeaker`` only adapts a blocking chunk iterator onto the
event loop and strips a WAV header if the provider ignored
``container=none``. Validation and generation are injected (DIP), so this
class never changes when voices or providers change (OCP).
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import threading
from collections.abc import Callable, Iterable

logger = logging.getLogger("tts")

_TTS_TIMEOUT = 30.0


class ThreadedTextToSpeechSpeaker:
    """Stream blocking audio generation without blocking the asyncio event loop."""

    def __init__(self, chunk_factory: Callable[[str, str], Iterable[bytes]]):
        """Store the audio-chunk generator used for each speech request."""
        self._chunk_factory = chunk_factory

    async def speak(self, text: str, voice: str, send_bytes) -> None:
        """Run audio generation on a worker thread and forward chunks to the callback."""
        # Validation is intentionally delegated: callers (facade) validate
        # text+voice once; this bridge only streams.
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        stop = threading.Event()

        def produce():
            """Generate chunks on the worker and safely enqueue chunks/errors for asyncio."""
            ctx = contextvars.copy_context()
            try:
                def _run_in_context():
                    for chunk in self._chunk_factory(text, voice):
                        if stop.is_set():
                            break
                        loop.call_soon_threadsafe(queue.put_nowait, chunk)
                ctx.run(_run_in_context)
            except Exception as error:
                loop.call_soon_threadsafe(queue.put_nowait, error)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        thread = threading.Thread(target=produce, daemon=True)
        thread.start()

        first = True
        sent = 0
        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(queue.get(), timeout=_TTS_TIMEOUT)
                except asyncio.TimeoutError:
                    logger.warning("tts timed out for %r", text)
                    stop.set()
                    break
                if chunk is None:
                    break
                if isinstance(chunk, Exception):
                    raise chunk
                if first:
                    first = False
                    if chunk[:4] == b"RIFF":
                        chunk = chunk[44:]
                if chunk:
                    sent += len(chunk)
                    await send_bytes(chunk)
            if sent == 0 and not stop.is_set():
                logger.warning("no audio returned for %r", text)
        except asyncio.CancelledError:
            stop.set()
            raise
        finally:
            stop.set()
            thread.join(timeout=1.0)
