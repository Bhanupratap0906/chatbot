"""Realtime voice session — orchestrator only (SRP + DIP).

Split of the old 300-line ``VoiceSession`` god object:

* ``WsPublisher`` — websocket send adapter (ISP).
* ``TranscriptBuffer`` — pending-text accumulation (SRP).
* ``TurnManager`` — single-turn lifecycle: cancel/start/run, LLM stream,
  chunking, TTS, history.
* ``VoiceSession`` — pumps (browser->Deepgram, Deepgram->turn, keepalive)
  + greeting delegation (SRP: orchestration only).

``VoiceSession`` depends on ``LlmAssistant`` / ``AsyncSpeaker`` /
``ChunkSplitter`` ABCs (DIP), so fakes substitute cleanly (LSP). New
turn policies or publishers extend without editing callers (OCP).
"""

from __future__ import annotations

import asyncio
import json
import time

from fastapi import WebSocket, WebSocketDisconnect

from modules.common.costs import ConversationCostTracker
from modules.common.protocols import ChunkSplitter, ConversationHistory, LlmAssistant, MessagePublisher
from modules.common.settings import Settings, get_settings
from modules.llm.chunking import SentenceChunkSplitter
from modules.realtime.greeting import GreetingService
from modules.realtime.history import InMemoryHistory
from modules.tts.voices import VOICES


class WsPublisher(MessagePublisher):
    """ISP adapter: turn code needs only send_json/send_bytes."""

    def __init__(self, ws: WebSocket):
        """Wrap FastAPI's socket for turn/greeting code that only needs send operations."""
        self._ws = ws

    async def send_json(self, payload: dict) -> None:
        """Send a JSON event to the connected frontend; used by turn and greeting services."""
        await self._ws.send_json(payload)

    async def send_bytes(self, data: bytes) -> None:
        """Send a PCM audio chunk to the frontend; used by the TTS streaming callback."""
        await self._ws.send_bytes(data)


class TranscriptBuffer:
    """SRP: accumulates interim/final transcripts into one turn string."""

    def __init__(self):
        """Create an empty transcript accumulator; owned by each VoiceSession."""
        self._pending = ""

    def add_final(self, transcript: str) -> None:
        """Append a finalized STT segment; called when Deepgram emits final results."""
        self._pending = (self._pending + " " + transcript).strip()

    def take(self) -> str:
        """Return and clear the pending utterance; called before starting an assistant turn."""
        text = self._pending.strip()
        self._pending = ""
        return text


class TurnManager:
    """SRP: owns exactly one assistant turn at a time."""

    def __init__(
        self,
        publisher: MessagePublisher,
        llm: LlmAssistant,
        tts,
        history: ConversationHistory,
        splitter: ChunkSplitter,
        settings: Settings,
        voice_provider,
    ):
        """Store services and state required to process one assistant turn."""
        self._publisher = publisher
        self._llm = llm
        self._tts = tts
        self._history = history
        self._splitter = splitter
        self._settings = settings
        self._voice_provider = voice_provider
        self._task: asyncio.Task | None = None

    @property
    def task(self):
        """Expose the current task to VoiceSession and controller cleanup."""
        return self._task

    async def start(self, text: str) -> None:
        """Replace any current response with a new turn; used when a caller starts a turn."""
        await self.cancel()
        self._task = asyncio.create_task(self.run(text))

    async def cancel(self) -> None:
        """Stop the current response and notify the client; used for barge-in and disconnect handling."""
        task = self._task
        self._task = None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as error:
                print("Cancelled turn raised:", error)
            await self._publisher.send_json({"type": "interrupt"})

    async def _speak(self, text: str, elapsed) -> None:
        """Stream one text chunk as audio and log first-audio latency; used by ``run``."""
        first_logged = False
        publisher = self._publisher
        voice = self._voice_provider()

        async def send_with_log(chunk: bytes):
            """Log the first audio chunk, then forward every chunk to the browser."""
            nonlocal first_logged
            if not first_logged:
                first_logged = True
                print(f"[tts] first audio at {elapsed()} for {text!r}")
            await publisher.send_bytes(chunk)

        await self._tts.speak(text, voice, send_with_log)

    async def run(self, text: str) -> None:
        """Stream an LLM answer, speak sentence chunks, and save the completed turn; scheduled by VoiceSession."""
        started = time.perf_counter()

        def elapsed():
            """Format elapsed turn time for latency logs."""
            return f"{time.perf_counter() - started:.2f}s"

        try:
            print(f"[turn] start: {text!r}")
            await self._publisher.send_json({"type": "user", "text": text})
            await self._publisher.send_json({"type": "answer_start"})

            buffer = ""
            spoken: list[str] = []
            first_token = True
            stream = self._llm.stream(text, self._history.snapshot())

            while True:
                try:
                    piece = await asyncio.wait_for(stream.__anext__(), timeout=self._settings.llm_timeout)
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    raise RuntimeError(f"Gemini produced nothing in {self._settings.llm_timeout}s")

                if first_token:
                    first_token = False
                    print(f"[turn] first LLM token at {elapsed()}")

                await self._publisher.send_json({"type": "token", "text": piece})
                buffer += piece
                chunk, buffer = self._splitter.split(buffer, is_first=not spoken)
                if chunk:
                    spoken.append(chunk)
                    await self._speak(chunk, elapsed)

            if buffer.strip():
                spoken.append(buffer.strip())
                await self._speak(buffer.strip(), elapsed)

            answer = " ".join(spoken)
            self._history.add_turn(text, answer)
            print(f"[turn] done at {elapsed()}: {answer!r}")
            await self._publisher.send_json({"type": "answer_end"})

        except asyncio.CancelledError:
            print(f"[turn] cancelled at {elapsed()}")
            raise
        except Exception as error:
            print(f"[turn] failed at {elapsed()}: {error!r}")
            try:
                await self._publisher.send_json({"type": "error", "message": str(error)})
                await self._publisher.send_json({"type": "answer_end"})
            except Exception:
                pass


class VoiceSession:
    """Orchestrator: pumps + greeting. Turn work delegated to ``TurnManager``."""

    def __init__(
        self,
        ws: WebSocket,
        dg,
        voice: str = "flux-hannah-en",
        llm: LlmAssistant | None = None,
        tts=None,
        history: ConversationHistory | None = None,
        splitter: ChunkSplitter | None = None,
        settings: Settings | None = None,
    ):
        """Wire websocket, STT, LLM, TTS, history, and turn services for one live call; built by the voice websocket route."""
        self.ws = ws
        self.dg = dg
        self.voice = voice

        self._settings = settings or get_settings()
        self.costs = ConversationCostTracker(self._settings)
        # DIP: concrete defaults keep old ``VoiceSession(ws, dg)`` working,
        # but tests/callers may inject fakes (LSP).
        if llm is None:
            from modules.llm.llm_service import LLMresponse
            llm = LLMresponse(cost_tracker=self.costs)
        if tts is None:
            from modules.tts.tts_service import TtsService
            tts = TtsService(cost_tracker=self.costs)

        self.llm = llm
        self.tts = tts
        self.history: ConversationHistory = history or InMemoryHistory(window=self._settings.history_window)
        self._splitter = splitter or SentenceChunkSplitter()
        self._publisher = WsPublisher(ws)
        self._buffer = TranscriptBuffer()
        self.turns = TurnManager(
            publisher=self._publisher,
            llm=self.llm,
            tts=self.tts,
            history=self.history,
            splitter=self._splitter,
            settings=self._settings,
            voice_provider=lambda: self.voice,
        )
        self._greeting = GreetingService(
            publisher=self._publisher,
            speaker=_SpeakerAdapter(self),
            history=self.history,
            greeting_text=self._settings.greeting_text,
            voice=self.voice,
        )
    @property
    def turn(self):
        """Return the active turn task for controller cleanup."""
        return self.turns.task

    # ---------- browser -> Deepgram ----------

    async def pump_client(self):
        """Forward browser audio to Deepgram and handle voice/interrupt events; run as a session task."""
        while True:
            message = await self.ws.receive()
            if message["type"] == "websocket.disconnect":
                raise WebSocketDisconnect()
            if message.get("bytes") is not None:
                self.costs.add_stt_audio(
                    len(message["bytes"]), self._settings.input_sample_rate
                )
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
                    self._greeting._voice = voice
            elif payload.get("type") == "interrupt":
                await self.cancel_turn()

    # ---------- Deepgram -> pipeline ----------

    async def pump_deepgram(self):
        """Translate Deepgram events into frontend transcripts and assistant turns; run as a session task."""
        async for raw in self.dg:
            event = json.loads(raw)
            kind = event.get("type")
            if kind == "SpeechStarted":
                if self._settings.barge_in:
                    print("[dg] speech started -> cancelling turn")
                    await self.cancel_turn()
            elif kind == "Results":
                alt = event["channel"]["alternatives"][0]
                transcript = alt.get("transcript", "").strip()
                is_final = event.get("is_final", False)
                speech_final = event.get("speech_final", False)
                if transcript:
                    await self._publisher.send_json(
                        {"type": "transcript", "text": transcript, "final": is_final}
                    )
                if is_final and transcript:
                    self._buffer.add_final(transcript)
                if speech_final:
                    print("[dg] speech_final")
                    await self.start_turn()
            elif kind == "UtteranceEnd":
                print("[dg] utterance end")
                await self.start_turn()
            elif kind == "Error" or event.get("error"):
                print("[dg] error:", raw)

    async def keepalive(self):
        """Periodically keep Deepgram's realtime socket alive; run alongside the two websocket pumps."""
        while True:
            await asyncio.sleep(8)
            await self.dg.send(json.dumps({"type": "KeepAlive"}))

    # ---------- turn delegation ----------

    async def start_turn(self):
        """Consume buffered speech and schedule its assistant response; called on final utterance events."""
        text = self._buffer.take()
        if not text:
            return
        await self.turns.start(text)

    async def cancel_turn(self):
        """Cancel the active assistant response; called for client interrupts and barge-in."""
        await self.turns.cancel()

    async def send_greeting(self):
        """Send the configured welcome message using the selected voice; launched once per connection."""
        # Rebuild with current voice (voice may change via config).
        self._greeting._voice = self.voice
        await self._greeting.send()


class _SpeakerAdapter:
    """Adapts TTS for GreetingService (DIP). Respects the voice passed in."""

    def __init__(self, session: VoiceSession):
        """Bind greeting speech to the session's TTS and websocket publisher."""
        self._session = session

    async def speak(self, text: str, voice: str) -> None:
        """Adapt the greeting speaker interface to session TTS and log first audio latency."""
        started = time.perf_counter()

        def elapsed():
            """Format greeting-audio elapsed time for latency logs."""
            return f"{time.perf_counter() - started:.2f}s"

        first_logged = False

        async def send_with_log(chunk: bytes):
            """Log the first greeting audio chunk and forward it to the browser."""
            nonlocal first_logged
            if not first_logged:
                first_logged = True
                print(f"[tts] first audio at {elapsed()} for {text!r}")
            await self._session._publisher.send_bytes(chunk)

        await self._session.tts.speak(text, voice, send_with_log)
