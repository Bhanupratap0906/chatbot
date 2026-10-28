"""Realtime voice session — orchestrator only (SRP + DIP).

Split of the old 300-line ``RealtimeVoiceSession`` god object:

* ``WebSocketMessagePublisher`` — websocket send adapter (ISP).
* ``PendingTranscriptBuffer`` — pending-text accumulation (SRP).
* ``AssistantTurnManager`` — single-turn lifecycle: cancel/start/run, LLM stream,
  chunking, TTS, history.
* ``RealtimeVoiceSession`` — pumps (browser->Deepgram, Deepgram->turn, keepalive)
  + greeting delegation (SRP: orchestration only).

``RealtimeVoiceSession`` depends on ``LlmAssistant`` / ``AsyncSpeaker`` /
``ChunkSplitter`` ABCs (DIP), so fakes substitute cleanly (LSP). New
turn policies or publishers extend without editing callers (OCP).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid

from fastapi import WebSocket, WebSocketDisconnect

from modules.common.conversation_cost_tracker import ConversationCostTracker
from modules.common.backend_contracts import ChunkSplitter, ConversationHistory, LlmAssistant, MessagePublisher
from modules.common.settings import Settings, get_settings
from modules.common.redact import redact
from modules.llm.sentence_chunk_splitter import SentenceChunkSplitter
from modules.realtime.greeting_service import GreetingService
from modules.realtime.conversation_history import InMemoryConversationHistory
from modules.realtime.conversation_coordinator import ConversationCoordinator, BookingState
from modules.tts.voice_catalog import VOICES

logger = logging.getLogger("session")


class WebSocketMessagePublisher(MessagePublisher):
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


class PendingTranscriptBuffer:
    """SRP: accumulates interim/final transcripts into one turn string."""

    def __init__(self):
        """Create an empty transcript accumulator; owned by each RealtimeVoiceSession."""
        self._pending = ""

    def add_final_transcript(self, transcript: str) -> None:
        """Append a finalized STT segment; called when Deepgram emits final results."""
        self._pending = (self._pending + " " + transcript).strip()

    def take_pending_transcript(self) -> str:
        """Return and clear the pending utterance; called before starting an assistant turn."""
        text = self._pending.strip()
        self._pending = ""
        return text



def _sanitize_text_for_speech(text: str) -> str:
    """Clean LLM output before TTS: remove format hints, ISO dates, markdown."""
    import re

    if not text:
        return text
    # Normalize special spaces.
    text = text.replace("\u202f", " ").replace("\u00a0", " ")
    # Insert a space after . ? ! when followed by a letter.
    import re as _re

    text = _re.sub(r"([.?!])([A-Za-z])", r"\1 \2", text)
    # If more than one question, keep only up to the first question sentence.
    if text.count("?") > 1:
        idx = text.find("?")
        text = text[: idx + 1]
    # Remove parenthetical/inline format hints like (YYYY-MM-DD) or YYYY/MM/DD.
    text = re.sub(r"\(?\b(YYYY|yyyy)[-/ ]?(MM|mm)[-/ ]?(DD|dd)\)?", "", text)
    # Convert ISO dates to spoken dates.
    def _replace_iso(match):
        try:
            from datetime import datetime

            value = datetime.strptime(match.group(0), "%Y-%m-%d")
            day = value.day
            suffix = "th"
            if day % 10 == 1 and day != 11:
                suffix = "st"
            elif day % 10 == 2 and day != 12:
                suffix = "nd"
            elif day % 10 == 3 and day != 13:
                suffix = "rd"
            return f"{value.strftime('%B')} {day}{suffix}, {value.year}"
        except ValueError:
            return match.group(0)

    text = re.sub(r"\b(\d{4})-(\d{2})-(\d{2})\b", _replace_iso, text)
    # Strip markdown characters and list markers at start of lines.
    lines = []
    for line in text.splitlines():
        line = re.sub(r"^[\s>*\-+]+", "", line)
        line = line.replace("*", "").replace("#", "").replace("`", "").replace("_", "")
        lines.append(line)
    text = "\n".join(lines)
    # Collapse multiple spaces.
    text = re.sub(r" {2,}", " ", text).strip()
    return text

class AssistantTurnManager:
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
        on_complete=None,
        ws=None,
        dg=None,
    ):
        """Store services and state required to process one assistant turn."""
        self._publisher = publisher
        self._llm = llm
        self._tts = tts
        self._history = history
        self._splitter = splitter
        self._settings = settings
        self._voice_provider = voice_provider
        self._on_complete = on_complete
        self._ws = ws
        self._dg = dg
        self._task: asyncio.Task | None = None
        self._current_text = ""
        self._hangup = False

    @property
    def task(self):
        """Expose the current task to RealtimeVoiceSession and controller cleanup."""
        return self._task

    async def start_assistant_turn(self, text: str) -> None:
        """Replace any current response with a new turn; used when a caller starts a turn."""
        await self.cancel_assistant_turn()
        self._task = asyncio.create_task(self.run_assistant_turn(text))

    async def cancel_assistant_turn(self) -> None:
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
                logger.warning("cancelled turn raised: %r", error)
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
                logger.info("tts first audio at %s for %r", elapsed(), redact(text))
            await publisher.send_bytes(chunk)

        await self._tts.speak(text, voice, send_with_log)

    async def run_assistant_turn(self, text: str) -> None:
        """Stream an LLM answer, speak sentence chunks, and save the completed turn; scheduled by RealtimeVoiceSession."""
        started = time.perf_counter()

        def elapsed():
            """Format elapsed turn time for latency logs."""
            return f"{time.perf_counter() - started:.2f}s"

        try:
            logger.info("turn start: %r", redact(text))
            self._current_text = text
            await self._publisher.send_json({"type": "user", "text": text})
            await self._publisher.send_json({"type": "answer_start"})

            buffer = ""
            spoken: list[str] = []
            first_token = True
            self._rate_limited = False
            workflow = getattr(self._llm, "workflow", None)
            stream = workflow.stream_conversation_turn(text, self._history.snapshot()) if workflow else self._llm.stream_conversation_turn(text, self._history.snapshot())
            hangup = False

            while True:
                try:
                    piece = await asyncio.wait_for(stream.__anext__(), timeout=self._settings.llm_timeout)
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    raise RuntimeError(f"LLM produced nothing in {self._settings.llm_timeout}s")

                if first_token:
                    first_token = False
                    logger.info("turn first LLM token at %s", elapsed())

                if piece == "HANGUP_CALL":
                    hangup = True
                    break
                if piece not in ("RATE_LIMIT_BUSY",):
                    piece = _sanitize_text_for_speech(piece)
                    if not piece:
                        continue

                if piece == "RATE_LIMIT_BUSY":
                    reply = "The clinic assistant is busy right now. Please try again in a moment."
                    await self._publisher.send_json({"type": "token", "text": reply})
                    await self._speak(reply, elapsed)
                    self._rate_limited = True
                    break

                await self._publisher.send_json({"type": "token", "text": piece})
                buffer = (buffer + " " + piece).strip() if buffer else piece
                chunk, buffer = self._splitter.split(buffer, is_first=not spoken)
                if chunk:
                    spoken.append(chunk)
                    await self._speak(chunk, elapsed)

            if hangup:
                message = "I’ll end this call now so you can speak with a team member. Goodbye."
                self._hangup = True
                if workflow:
                    workflow.hangup_requested = True
                await self._publisher.send_json({"type": "token", "text": message})
                await self._speak(message, elapsed)
                await self._publisher.send_json({"type": "answer_end"})
                self._current_text = ""
                await self._publisher.send_json({"type": "hangup"})
                if self._ws is not None:
                    await self._ws.close(code=1000, reason="Human assistance requested")
                try:
                    if self._dg is not None:
                        await self._dg.send(json.dumps({"type": "CloseStream"}))
                except Exception:
                    pass
                if self._dg is not None:
                    await self._dg.close()
                self._current_text = ""
                return

            if buffer.strip() and not hangup:
                spoken.append(buffer.strip())
                await self._speak(buffer.strip(), elapsed)

            answer = " ".join(spoken)
            if workflow:
                if not getattr(self, "_rate_limited", False):
                    self._history.replace_snapshot(workflow.history_messages)
            elif not workflow:
                self._history.add_turn(text, answer)
            self._rate_limited = False
            self._current_text = ""
            logger.info("turn done at %s: %r", elapsed(), redact(answer))
            if self._on_complete is not None:
                try:
                    await self._on_complete(answer)
                except Exception as error:
                    logger.warning("on_complete callback failed: %r", error)
            await self._publisher.send_json({"type": "answer_end"})

        except asyncio.CancelledError:
            logger.info("turn cancelled at %s", elapsed())
            raise
        except Exception as error:
            logger.warning("turn failed at %s: %r", elapsed(), error)
            try:
                message = str(error).lower()
                if "rate_limit" in message or "rate limit" in message or getattr(error, "status_code", None) == 429:
                    reply = "The clinic assistant is busy right now. Please try again in a moment."
                    await self._publisher.send_json({"type": "token", "text": reply})
                    await self._speak(reply, elapsed)
                else:
                    await self._publisher.send_json({"type": "error", "message": str(error)})
                await self._publisher.send_json({"type": "answer_end"})
            except Exception:
                pass


class RealtimeVoiceSession:
    """Orchestrator: pumps + greeting. Turn work delegated to ``AssistantTurnManager``."""

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
        portal_token: str | None = None,
        portal_location_id: str | None = None,
    ):
        """Wire websocket, STT, LLM, TTS, history, and turn services for one live call; built by the voice websocket route."""
        self.ws = ws
        self.dg = dg
        self.voice = voice
        self._settings = settings or get_settings()
        self.costs = ConversationCostTracker(self._settings)
        self.api_calls: list[dict] = []
        from modules.portal.portal_http_client import set_api_audit
        self._api_audit_token = set_api_audit(self.api_calls)
        self.session_id = str(uuid.uuid4())
        self.portal_token = portal_token
        self.portal_location_id = portal_location_id
        from modules.portal.token_store import set_portal_token
        if portal_token:
            set_portal_token(portal_token, location_id=portal_location_id)
        # DIP: concrete defaults keep old ``RealtimeVoiceSession(ws, dg)`` working,
        # but tests/callers may inject fakes (LSP).
        if llm is None:
            from modules.workflow.conversation_workflow_graph import ConversationWorkflow
            llm = ConversationWorkflow(cost_tracker=self.costs, thread_id=self.session_id)
        self.workflow = getattr(llm, "workflow", llm)
        if hasattr(self.workflow, "bind_to_conversation_thread"):
            self.workflow.bind_to_conversation_thread(self.session_id)
        else:
            self.workflow.thread_id = self.session_id
        if tts is None:
            from modules.tts.text_to_speech_service import TextToSpeechService
            tts = TextToSpeechService(cost_tracker=self.costs)

        self.llm = llm
        self.tts = tts
        self.history: ConversationHistory = history or InMemoryConversationHistory(window=self._settings.history_window)
        self._splitter = splitter or SentenceChunkSplitter()
        self._publisher = WebSocketMessagePublisher(ws)
        self._buffer = PendingTranscriptBuffer()
        self.turns = AssistantTurnManager(
            publisher=self._publisher,
            llm=self.llm,
            tts=self.tts,
            history=self.history,
            splitter=self._splitter,
            settings=self._settings,
            voice_provider=lambda: self.voice,
            on_complete=self._handle_turn_completion,
            ws=self.ws,
            dg=self.dg,
        )
        self._no_answer_task: asyncio.Task | None = None
        self._last_assistant_text: str = ""
        self._no_answer_repeats: int = 0
        self._greeting = GreetingService(
            publisher=self._publisher,
            speaker=_SpeakerAdapter(self),
            history=self.history,
            greeting_text=self._settings.greeting_text,
            voice=self.voice,
        )
        # Shared session coordinator for Speaking Agent + Booking Agent
        self.coordinator = ConversationCoordinator(session_id=self.session_id)
    @property
    def turn(self):
        """Return the active turn task for controller cleanup."""
        return self.turns.task

    # ---------- browser -> Deepgram ----------

    async def pump_browser_audio_to_deepgram(self):
        """Forward browser audio to Deepgram and handle voice/interrupt events; run as a session task."""
        try:
            while True:
                try:
                    message = await self.ws.receive()
                except Exception:
                    if self.turns._hangup:
                        return
                    raise
                if message["type"] == "websocket.disconnect":
                    if self.turns._hangup:
                        return
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
                    await self.cancel_active_assistant_turn()
        finally:
            try:
                from modules.portal.portal_http_client import reset_api_audit
                reset_api_audit(self._api_audit_token)
            except Exception:
                pass
            logger.info("API CALL SUMMARY (%d calls):", len(self.api_calls))
            for idx, call in enumerate(self.api_calls, 1):
                logger.info(
                    "API #%d %s %s -> %s request=%s response=%s",
                    idx,
                    call.get("method"),
                    call.get("url"),
                    call.get("status"),
                    redact(call.get("request")),
                    redact(call.get("response")),
                )

    # ---------- Deepgram -> pipeline ----------

    async def _process_deepgram_event(self, raw: str) -> None:
        """Handle one Deepgram event (extracted for reconnect support)."""
        event = json.loads(raw)
        kind = event.get("type")
        if kind == "SpeechStarted":
            if self._settings.barge_in:
                logger.info("stt speech started -> cancelling turn")
                self._cancel_prompt_caller_after_silence()
                await self.cancel_active_assistant_turn()
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
                self._buffer.add_final_transcript(transcript)
            if speech_final:
                logger.info("stt speech_final")
                await self.start_assistant_turn_from_buffer()
        elif kind == "UtteranceEnd":
            logger.info("stt utterance end")
            await self.start_assistant_turn_from_buffer()
        elif kind == "Error" or event.get("error"):
            logger.warning("stt error: %s", raw)

    async def _speak_fallback_and_escalate(self) -> None:
        """Speak the unrecoverable-STT fallback and escalate the call."""
        fallback = "I'm having trouble, let me connect you to the front desk."
        try:
            await self._publisher.send_json({"type": "transcript", "text": fallback, "final": True})
        except Exception:
            pass
        try:
            await self.tts.speak(fallback, self.voice, self._publisher.send_bytes)
        except Exception as error:
            logger.warning("fallback tts failed: %r", error)
        self.turns._hangup = True
        self.hangup_requested = True

    async def pump_deepgram_events_to_turns(self):
        """Translate Deepgram events into frontend transcripts and assistant turns; run as a session task."""
        attempts = 0
        while True:
            try:
                async for raw in self.dg:
                    await self._process_deepgram_event(raw)
                break
            except Exception as error:
                logger.warning("deepgram socket dropped: %r", error)
                if attempts >= 1:
                    await self._speak_fallback_and_escalate()
                    break
                attempts += 1
                try:
                    from modules.stt.deepgram_realtime_connector import DeepgramRealtimeConnector

                    connector = DeepgramRealtimeConnector(
                        api_key=self._settings.deepgram_api_key,
                        input_sample_rate=self._settings.input_sample_rate,
                    )
                    self.dg = await connector.connect_to_deepgram_realtime()
                except Exception as reconnect_error:
                    logger.error("deepgram reconnect failed: %r", reconnect_error)
                    await self._speak_fallback_and_escalate()
                    break

    async def keep_deepgram_connection_alive(self):
        """Periodically keep Deepgram's realtime socket alive; run alongside the two websocket pumps."""
        while True:
            await asyncio.sleep(8)
            await self.dg.send(json.dumps({"type": "KeepAlive"}))

    # ---------- turn delegation ----------

    _BACKCHANNELS = {"yeah", "yes", "okay", "ok", "uh-huh", "mm", "hmm", "right", "yep"}

    async def start_assistant_turn_from_buffer(self):
        """Consume buffered speech and schedule its assistant response; called on final utterance events."""
        self._cancel_prompt_caller_after_silence()
        text = self._buffer.take_pending_transcript()
        if not text:
            return
        # Record utterance with coordinator for intent-aware routing
        await self.coordinator.receive_utterance(text)
        # Ignore backchannels/short utterances while the agent is speaking.
        words = text.lower().split()
        if self.turns.task is not None and not self.turns.task.done():
            if len(words) <= 2 or all(w.strip(".,!?") in self._BACKCHANNELS for w in words):
                # If a confirmation is pending, treat affirmative backchannel as confirmation
                if self.coordinator.state.booking_status == BookingState.AWAITING_PATIENT_CONFIRMATION:
                    affirmative_words = {"yes", "yeah", "yep", "correct", "right", "sure", "go ahead", "confirm"}
                    is_affirmative = any(w.strip(".,!?") in affirmative_words for w in words)
                    if is_affirmative:
                        await self.coordinator.confirm_pending_booking()
                        # Speak acknowledgment without restarting full turn
                        await self._publisher.send_json({"type": "token", "text": "Thank you for confirming. I'll finalize that now."})
                        return
                logger.info("ignoring backchannel/short utterance while speaking: %r", redact(text))
                return
        await self.turns.start_assistant_turn(text)

    async def cancel_active_assistant_turn(self):
        """Cancel the active assistant response; called for client interrupts and barge-in."""
        self._cancel_prompt_caller_after_silence()
        await self.turns.cancel_assistant_turn()
    def _cancel_prompt_caller_after_silence(self) -> None:
        task = self._no_answer_task
        self._no_answer_task = None
        if task and not task.done():
            task.cancel()

    async def _handle_turn_completion(self, answer: str) -> None:
        self._last_assistant_text = (answer or "").strip()
        self._cancel_prompt_caller_after_silence()
        self._no_answer_repeats = 0
        if self._last_assistant_text:
            self._no_answer_task = asyncio.create_task(self._prompt_caller_after_silence())

    async def _prompt_caller_after_silence(self) -> None:
        try:
            await asyncio.sleep(self._settings.no_answer_timeout)
            if self._no_answer_repeats >= 1:
                return
            self._no_answer_repeats += 1
            prompt = self._last_assistant_text
            await self._publisher.send_json({"type": "token", "text": prompt})
            voice = self.voice
            async def send_with_log(chunk: bytes):
                await self._publisher.send_bytes(chunk)
            await self.tts.speak(prompt, voice, send_with_log)
        except asyncio.CancelledError:
            pass
        except Exception as error:
            logger.warning("no-answer watchdog failed: %r", error)

    async def send_configured_greeting(self):
        """Send the configured welcome message using the selected voice; launched once per connection."""
        # Rebuild with current voice (voice may change via config).
        self._greeting._voice = self.voice
        await self._greeting.send()

class _SpeakerAdapter:
    """Adapts TTS for GreetingService (DIP). Respects the voice passed in."""

    def __init__(self, session: RealtimeVoiceSession):
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
                logger.info("tts first audio at %s for %r", elapsed(), redact(text))
            await self._session._publisher.send_bytes(chunk)

        await self._session.tts.speak(text, voice, send_with_log)
