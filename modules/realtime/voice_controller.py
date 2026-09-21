import asyncio
import json
import os
import re
import threading
import time
from urllib.parse import urlencode

import websockets
from dotenv import load_dotenv
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from modules.llm.llm_service import LLMresponse
from modules.tts.tts_service import TtsService, VOICES, SAMPLE_RATE as TTS_SAMPLE_RATE


load_dotenv()


router = APIRouter(tags=["Realtime"])


# Must match INPUT_SAMPLE_RATE in the frontend (index.html), which forces its
# AudioContext to this rate before sending PCM here. If the two ever drift
# apart, Deepgram will get PCM at the wrong rate and transcripts will be
# empty or garbled even though the pipeline "looks" connected end to end.
INPUT_SAMPLE_RATE = 16000

# Cancel a turn when the caller talks over the assistant. Leave this off until
# the happy path works: with speakers (no headphones) the mic hears the
# assistant's own voice and kills every turn before you hear anything.
BARGE_IN = os.getenv("BARGE_IN", "false").lower() == "true"

# If Gemini produces nothing in this long, fail loudly instead of hanging.
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "20"))


DEEPGRAM_LISTEN_URL = "wss://api.deepgram.com/v1/listen?" + urlencode(
    {
        "model": "nova-3",
        "language": "en",
        "encoding": "linear16",
        "sample_rate": INPUT_SAMPLE_RATE,
        "channels": 1,
        "smart_format": "true",
        "punctuate": "true",
        "interim_results": "true",
        "vad_events": "true",
        "endpointing": "300",
        "utterance_end_ms": "1000",
    }
)

GREETING_TEXT = (
    "Hi, thanks for calling our dental clinic. "
    "I can help with appointments or questions about the clinic. "
    "How can I help you today?"
)

SENTENCE_END = re.compile(r"[\.\!\?](?=\s|$)")

# Get the very first phrase out fast, then use bigger chunks: every chunk is a
# separate Deepgram request, so many tiny ones are slower overall, not faster.
FIRST_CHUNK_CHARS = 60
LATER_CHUNK_CHARS = 220


def _split_chunk(buffer: str, is_first: bool):
    """Return (ready_to_speak, remainder)."""

    limit = FIRST_CHUNK_CHARS if is_first else LATER_CHUNK_CHARS

    matches = list(SENTENCE_END.finditer(buffer))

    if matches:
        cut = matches[-1].end()
        return buffer[:cut].strip(), buffer[cut:]

    if len(buffer) >= limit:
        cut = buffer.rfind(" ", 0, limit)
        cut = cut if cut > 0 else limit
        return buffer[:cut].strip(), buffer[cut:]

    return "", buffer


async def _connect_deepgram(api_key: str):
    headers = {"Authorization": f"Token {api_key}"}

    try:
        return await websockets.connect(
            DEEPGRAM_LISTEN_URL, additional_headers=headers, max_size=None
        )
    except TypeError:
        # websockets < 14 uses extra_headers
        return await websockets.connect(
            DEEPGRAM_LISTEN_URL, extra_headers=headers, max_size=None
        )


class VoiceSession:

    def __init__(self, ws: WebSocket, dg, voice: str = "flux-hannah-en"):
        self.ws = ws
        self.dg = dg
        self.voice = voice

        self.llm = LLMresponse()
        self.tts = TtsService()

        self.history = []
        self.pending = ""
        self.turn = None

    # ---------- browser -> Deepgram ----------

    async def pump_client(self):
        while True:
            message = await self.ws.receive()

            if message["type"] == "websocket.disconnect":
                raise WebSocketDisconnect()

            if message.get("bytes") is not None:
                await self.dg.send(message["bytes"])
                continue

            text = message.get("text")

            if not text:
                continue

            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                continue

            if payload.get("type") == "config":
                voice = payload.get("voice")

                if voice in VOICES:
                    self.voice = voice

            elif payload.get("type") == "interrupt":
                await self.cancel_turn()

    # ---------- Deepgram -> pipeline ----------

    async def pump_deepgram(self):
        async for raw in self.dg:
            event = json.loads(raw)
            kind = event.get("type")

            if kind == "SpeechStarted":
                if BARGE_IN:
                    print("[dg] speech started -> cancelling turn")
                    await self.cancel_turn()

            elif kind == "Results":
                alt = event["channel"]["alternatives"][0]
                transcript = alt.get("transcript", "").strip()
                is_final = event.get("is_final", False)
                speech_final = event.get("speech_final", False)

                if transcript:
                    await self.ws.send_json(
                        {"type": "transcript", "text": transcript, "final": is_final}
                    )

                if is_final and transcript:
                    self.pending = (self.pending + " " + transcript).strip()

                if speech_final:
                    print("[dg] speech_final")
                    await self.start_turn()

            elif kind == "UtteranceEnd":
                print("[dg] utterance end")
                await self.start_turn()

            elif kind == "Error" or event.get("error"):
                print("[dg] error:", raw)

    async def keepalive(self):
        while True:
            await asyncio.sleep(8)
            await self.dg.send(json.dumps({"type": "KeepAlive"}))

    # ---------- a single assistant turn ----------

    async def start_turn(self):
        text = self.pending.strip()
        self.pending = ""

        if not text:
            return

        await self.cancel_turn()
        self.turn = asyncio.create_task(self.run_turn(text))

    async def cancel_turn(self):
        turn = self.turn
        self.turn = None

        if turn and not turn.done():
            turn.cancel()

            try:
                await turn
            except asyncio.CancelledError:
                pass
            except Exception as error:
                print("Cancelled turn raised:", error)

            await self.ws.send_json({"type": "interrupt"})

    async def run_turn(self, text: str):
        started = time.perf_counter()

        def elapsed():
            return f"{time.perf_counter() - started:.2f}s"

        try:
            print(f"[turn] start: {text!r}")

            await self.ws.send_json({"type": "user", "text": text})
            await self.ws.send_json({"type": "answer_start"})

            buffer = ""
            spoken = []
            first_token = True

            stream = self.llm.stream(text, self.history)

            while True:
                try:
                    piece = await asyncio.wait_for(
                        stream.__anext__(), timeout=LLM_TIMEOUT
                    )
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    raise RuntimeError(
                        f"Gemini produced nothing in {LLM_TIMEOUT}s"
                    )

                if first_token:
                    first_token = False
                    print(f"[turn] first LLM token at {elapsed()}")

                await self.ws.send_json({"type": "token", "text": piece})

                buffer += piece
                chunk, buffer = _split_chunk(buffer, is_first=not spoken)

                if chunk:
                    spoken.append(chunk)
                    await self.speak(chunk, elapsed)

            if buffer.strip():
                spoken.append(buffer.strip())
                await self.speak(buffer.strip(), elapsed)

            answer = " ".join(spoken)

            self.history.append(("human", text))
            self.history.append(("ai", answer))
            self.history = self.history[-12:]

            print(f"[turn] done at {elapsed()}: {answer!r}")

            await self.ws.send_json({"type": "answer_end"})

        except asyncio.CancelledError:
            print(f"[turn] cancelled at {elapsed()}")
            raise
        except Exception as error:
            print(f"[turn] failed at {elapsed()}: {error!r}")

            try:
                await self.ws.send_json({"type": "error", "message": str(error)})
                await self.ws.send_json({"type": "answer_end"})
            except Exception:
                pass

    async def send_greeting(self):
        """Speak a fixed welcome message as soon as the call connects."""

        started = time.perf_counter()

        def elapsed():
            return f"{time.perf_counter() - started:.2f}s"

        try:
            print("[turn] greeting")

            await self.ws.send_json({"type": "answer_start"})
            await self.ws.send_json({"type": "token", "text": GREETING_TEXT})
            await self.speak(GREETING_TEXT, elapsed)

            self.history.append(("ai", GREETING_TEXT))

            await self.ws.send_json({"type": "answer_end"})

            print(f"[turn] greeting done at {elapsed()}")

        except Exception as error:
            print(f"[turn] greeting failed at {elapsed()}: {error!r}")

            try:
                await self.ws.send_json({"type": "answer_end"})
            except Exception:
                pass
    
    async def speak(self, text: str, elapsed):
        """Stream one chunk of TTS audio to the browser."""

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        stop = threading.Event()

        def produce():
            try:
                for chunk in self.tts.stream(text, self.voice):
                    if stop.is_set():
                        break
                    loop.call_soon_threadsafe(queue.put_nowait, chunk)
            except Exception as error:
                loop.call_soon_threadsafe(queue.put_nowait, error)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        threading.Thread(target=produce, daemon=True).start()

        first = True
        sent = 0

        try:
            while True:
                chunk = await queue.get()

                if chunk is None:
                    break

                if isinstance(chunk, Exception):
                    raise chunk

                if first:
                    first = False
                    print(f"[tts] first audio at {elapsed()} for {text!r}")

                    # strip a WAV header if Deepgram ignored container=none
                    if chunk[:4] == b"RIFF":
                        chunk = chunk[44:]

                if chunk:
                    sent += len(chunk)
                    await self.ws.send_bytes(chunk)

            if sent == 0:
                print(f"[tts] WARNING: no audio returned for {text!r}")
        finally:
            stop.set()


@router.websocket("/ws/voice")
async def voice_socket(ws: WebSocket):
    await ws.accept()

    api_key = os.getenv("DEEPGRAM_API_KEY")

    if not api_key:
        await ws.close(code=1011, reason="DEEPGRAM_API_KEY is not configured")
        return

    try:
        dg = await _connect_deepgram(api_key)
    except Exception as error:
        # Previously this raised uncaught, closing the socket with no
        # reason and no log line — from the browser it looked identical
        # to "connected but nothing happened". Now it's explicit.
        
        await ws.close(code=1011, reason="Failed to connect to speech-to-text service")
        return

   

    session = VoiceSession(ws, dg)

    await ws.send_json(
        {
            "type": "ready",
            "inputSampleRate": INPUT_SAMPLE_RATE,
            "ttsSampleRate": TTS_SAMPLE_RATE,
            "voices": VOICES,
        }
    )

    tasks = [
        asyncio.create_task(session.pump_client()),
        asyncio.create_task(session.pump_deepgram()),
        asyncio.create_task(session.keepalive()),
    ]

    # Runs once and finishes on its own — must not be in the FIRST_COMPLETED
    # race above, or the session tears down the instant the greeting ends.
    asyncio.create_task(session.send_greeting())

    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

        for task in pending:
            task.cancel()

        for task in done:
            exc = task.exception()

            if exc and not isinstance(exc, WebSocketDisconnect):
                print("Session ended:", repr(exc))
    finally:
        if session.turn and not session.turn.done():
            session.turn.cancel()

        try:
            await dg.send(json.dumps({"type": "CloseStream"}))
        except Exception:
            pass

        await dg.close()

        try:
            await ws.close()
        except Exception:
            pass

        print("[ws] /ws/voice connection closed")