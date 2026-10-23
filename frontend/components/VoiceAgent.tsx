"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Registerer, SessionState, UserAgent } from "sip.js";
import { PCM_WORKLET_SOURCE } from "@/lib/pcmWorkletSource";

/* =========================================================
   CONFIGURATION
========================================================= */

const SIP_WS =
  process.env.NEXT_PUBLIC_SIP_WS ?? "wss://sip.begindaily.in:7443/";

// Sample rate of the PCM sent to the backend. Must match INPUT_SAMPLE_RATE
// in modules/realtime, because Deepgram's /v1/listen connection there is
// opened with sample_rate=16000.
const INPUT_SAMPLE_RATE = 16000;

// Logs the peak level of the caller audio every second, plus a warning if
// it stays at pure silence.
const DEBUG_INPUT_LEVEL = true;

// How long to wait after answering before warning that no caller audio
// has arrived.
const NO_MEDIA_WARN_MS = 8000;

const SIP_DOMAIN    = process.env.NEXT_PUBLIC_SIPDOMAIN;
const SIP_USERNAME  = process.env.NEXT_PUBLIC_SIPUSERNAME;
const SIP_PASSWORD  = process.env.NEXT_PUBLIC_SIPPASSWORD;
// Logs inbound RTP byte counts 3s and 8s after answering.
const DEBUG_MEDIA_STATS = true;

function resolveVoiceWsUrl(): string {
  /** Resolve the backend realtime endpoint; called when opening the AI socket. */
  // WHY: frontend served from different hosts/ports (dev :3000, prod Nginx).
  // WHAT: prefers NEXT_PUBLIC_VOICE_WS_URL override, else builds ws(s)://host/ws/voice
  // so browser always hits the FastAPI realtime endpoint on the same origin.
  const override = process.env.NEXT_PUBLIC_VOICE_WS_URL;
  if (override) return override;
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${window.location.host}/ws/voice`;
}

type StatusType = "" | "success" | "error";

type Status = {
  text: string;
  type: StatusType;
  live: boolean;
};

export function VoiceAgent() {
  /** Manage SIP registration, the live-call WebSocket, audio routing, and call UI state. */
  /* ---------------- UI state ---------------- */
  const [sipDomain,   setSipDomain]   = useState(SIP_DOMAIN ?? "");
  const [sipUsername, setSipUsername] = useState(SIP_USERNAME ?? "");
  const [sipPassword, setSipPassword] = useState(SIP_PASSWORD ?? "");
  const [isRegistered, setIsRegistered] = useState(false);
  const [isStarting, setIsStarting] = useState(false);
  const [isDisconnecting, setIsDisconnecting] = useState(false);
  const [showHangup, setShowHangup] = useState(false);
  const [transcriptFinal, setTranscriptFinal] = useState("");
  const [transcriptInterim, setTranscriptInterim] = useState("");
  const [answerText, setAnswerText] = useState("");
  const [answerPlaceholder, setAnswerPlaceholder] = useState(true);
  const [status, setStatusState] = useState<Status>({
    text: "SIP not started",
    type: "",
    live: false,
  });

  /* ---------------- Refs (mutable SIP / voice state) ---------------- */
  // Using `any` for SIP.js session objects: their TS types vary between
  // Invitation/Session and add needless friction here.
  const remoteAudioRef = useRef<HTMLAudioElement | null>(null);
  const userAgentRef = useRef<any>(null);
  const registererRef = useRef<any>(null);
  const sipSessionRef = useRef<any>(null);
  const sipPeerConnectionRef = useRef<RTCPeerConnection | null>(null);
  const sipAudioSenderRef = useRef<RTCRtpSender | null>(null);
  const noMediaTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const callGenerationRef = useRef(0);

  const aiWsRef = useRef<WebSocket | null>(null);
  const aiAudioContextRef = useRef<AudioContext | null>(null);
  const aiWorkletContextRef = useRef<AudioContext | null>(null);
  const aiInputNodeRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const aiInputWorkletRef = useRef<AudioWorkletNode | null>(null);
  const aiSilentGainRef = useRef<GainNode | null>(null);
  const aiOutputDestinationRef = useRef<MediaStreamAudioDestinationNode | null>(
    null
  );
  const aiOutputGainRef = useRef<GainNode | null>(null);
  const aiOutputNextTimeRef = useRef(0);
  const aiOutputSourcesRef = useRef<AudioBufferSourceNode[]>([]);
  const ttsRateRef = useRef(24000);
  const carryRef = useRef<Uint8Array>(new Uint8Array(0));
  const finalTextRef = useRef("");
  const answerTextRef = useRef("");
  const testModeActiveRef = useRef(false);
  const testMicStreamRef = useRef<MediaStream | null>(null);

  // Credentials captured at Start SIP click time (avoids stale closures).
  const credsRef = useRef({ domain: "", username: "", password: "" });

  const setStatus = useCallback(
    (text: string, type: StatusType = "", live = false) => {
      setStatusState({ text, type, live });
    },
    []
  );

  /* =========================================================
     LOAD THE WORKLET (once per AudioContext)
     WHY: AudioWorklet downsamples/resamples mic/SIP audio to 16kHz Int16 PCM
     that Deepgram expects. Must load once per AudioContext, else addModule throws.
     WHAT: injects PCM_WORKLET_SOURCE via Blob URL into audioWorklet.
  ========================================================= */
  const ensurePCMWorklet = useCallback(async (audioContext: AudioContext) => {
    if (aiWorkletContextRef.current === audioContext) return;
    const blob = new Blob([PCM_WORKLET_SOURCE], {
      type: "application/javascript",
    });
    const url = URL.createObjectURL(blob);
    try {
      await audioContext.audioWorklet.addModule(url);
    } finally {
      URL.revokeObjectURL(url);
    }
    aiWorkletContextRef.current = audioContext;
  }, []);

  /* =========================================================
     GET OR CREATE THE SHARED AUDIO CONTEXT
     WHY: one shared AudioContext for input (SIP/mic) + TTS output keeps
     sample clocks in sync and avoids Chrome autoplay limits. Firefox rejects
     explicit sampleRate, so fallback to default + worklet resampling.
     WHAT: createInputAudioContext makes 16kHz context; getAudioContext reuses/
     resumes it (must resume after user gesture).
  ========================================================= */
  const createInputAudioContext = useCallback(() => {
    const isFirefox = /firefox/i.test(navigator.userAgent);
    if (!isFirefox) {
      try {
        return new AudioContext({ sampleRate: INPUT_SAMPLE_RATE });
      } catch (error) {
        console.warn("Browser rejected explicit sampleRate, using default:", error);
      }
    }
    return new AudioContext();
  }, []);

  const getAudioContext = useCallback(async () => {
    if (
      !aiAudioContextRef.current ||
      aiAudioContextRef.current.state === "closed"
    ) {
      aiAudioContextRef.current = createInputAudioContext();
      aiWorkletContextRef.current = null;
      console.log(
        "AI audio context sample rate:",
        aiAudioContextRef.current.sampleRate,
        aiAudioContextRef.current.sampleRate === INPUT_SAMPLE_RATE
          ? "(matches backend)"
          : `(worklet will downsample to ${INPUT_SAMPLE_RATE})`
      );
    }
    if (aiAudioContextRef.current.state === "suspended") {
      await Promise.race([
        aiAudioContextRef.current.resume(),
        new Promise((resolve) => setTimeout(resolve, 2000)),
      ]);
      if (aiAudioContextRef.current.state === "suspended") {
        console.warn(
          "AudioContext is still suspended — click Start SIP once so the " +
            "browser allows audio for this page."
        );
      }
    }
    return aiAudioContextRef.current;
  }, [createInputAudioContext]);

  /* =========================================================
     CONNECT TO /ws/voice
     WHY: this WS is the bridge to FastAPI VoiceSession: sends 16kHz PCM up,
     receives transcript/token/audio down. binaryType=arraybuffer lets us
     distinguish PCM (binary) from JSON control messages.
     WHAT: onopen sets status; onmessage routes ready/transcript/user/
     answer_start/token/answer_end/interrupt/error or enqueueTTS(audio);
     onerror/onclose surface disconnect reason (code/reason).
  ========================================================= */
  const connectAIWebSocket = useCallback(() => {
    const aiWsUrl = resolveVoiceWsUrl();
    console.log("Connecting Voice AI WebSocket:", aiWsUrl);
    const socket = new WebSocket(aiWsUrl);
    socket.binaryType = "arraybuffer";
    aiWsRef.current = socket;

    socket.onopen = () => {
      console.log("Voice AI WebSocket connected");
      setStatus("Caller connected — AI listening", "success", true);
    };

    socket.onmessage = (event) => {
      if (socket !== aiWsRef.current) return;

      if (typeof event.data !== "string") {
        enqueueTTS(event.data as ArrayBuffer);
        return;
      }

      const msg = JSON.parse(event.data as string);

      if (msg.type === "ready") {
        ttsRateRef.current = msg.ttsSampleRate || 24000;
        console.log("TTS sample rate:", ttsRateRef.current);
      } else if (msg.type === "transcript") {
        if (msg.final) {
          finalTextRef.current = (finalTextRef.current + " " + msg.text).trim();
          setTranscriptFinal(finalTextRef.current);
          setTranscriptInterim("");
        } else {
          setTranscriptFinal(finalTextRef.current);
          setTranscriptInterim(msg.text as string);
        }
      } else if (msg.type === "user") {
        finalTextRef.current = "";
        setTranscriptFinal(msg.text as string);
        setTranscriptInterim("");
        setStatus("AI is thinking...", "", true);
      } else if (msg.type === "answer_start") {
        answerTextRef.current = "";
        setAnswerText("");
        setAnswerPlaceholder(false);
      } else if (msg.type === "token") {
        answerTextRef.current += msg.text;
        setAnswerText(answerTextRef.current);
      } else if (msg.type === "answer_end") {
        setStatus("AI speaking / listening", "success", true);
      } else if (msg.type === "hangup") {
        setStatus("Connecting you to the clinic team — call ended", "success");
        void hangUpSIP();
      } else if (msg.type === "interrupt") {
        stopTTSPlayback();
      } else if (msg.type === "error") {
        console.error("AI error:", msg.message);
        setStatus("AI error: " + msg.message, "error");
      }
    };

    socket.onerror = (error) => {
      console.error("AI WebSocket error:", error);
      if (socket === aiWsRef.current) {
        setStatus("Voice AI connection failed", "error");
      }
    };

    socket.onclose = (event) => {
      console.log(
        "Voice AI WebSocket closed",
        "code:",
        event.code,
        "reason:",
        event.reason
      );
      if (socket !== aiWsRef.current) return;
      if (event.code !== 1000 && event.code !== 1005) {
        setStatus(
          `AI connection closed unexpectedly (code ${event.code}${event.reason ? ": " + event.reason : ""})`,
          "error"
        );
      }
      aiWsRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [setStatus]);

  /* =========================================================
     START AI AUDIO PIPELINE
     WHY: takes caller MediaStream (SIP RTP or test mic) and streams 16kHz PCM
     to backend. Silent GainNode keeps worklet running without feedback; level
     logging detects pure-silence (RTP not flowing) early.
     WHAT: get context -> ensure worklet -> MediaStreamSource -> pcm-processor
     -> silent gain -> destination; each worklet frame ws.send(Int16) + peak log.
  ========================================================= */
  const startVoiceAI = useCallback(
    async (remoteStream: MediaStream) => {
      console.log("Starting Voice AI with SIP audio");
      if (aiWsRef.current) {
        console.log("AI WebSocket already exists");
        return;
      }
      const generation = callGenerationRef.current;
      const audioContext = await getAudioContext();
      await ensurePCMWorklet(audioContext);
      if (generation !== callGenerationRef.current) {
        console.log("Call ended while starting Voice AI — aborting");
        return;
      }

      aiInputNodeRef.current =
        audioContext.createMediaStreamSource(remoteStream);

      aiInputWorkletRef.current = new AudioWorkletNode(
        audioContext,
        "pcm-processor",
        {
          numberOfInputs: 1,
          numberOfOutputs: 1,
          outputChannelCount: [1],
          channelCount: 1,
          channelCountMode: "explicit",
          // Forward less trailing silence so Deepgram can finalize a turn sooner.
          processorOptions: {
            targetRate: INPUT_SAMPLE_RATE,
            trailingSilenceMs: 700,
          },
        }
      );

      let frameCount = 0;
      let peak = 0;
      let silentSeconds = 0;

      aiInputWorkletRef.current.port.onmessage = (event) => {
        if (aiWsRef.current && aiWsRef.current.readyState === WebSocket.OPEN) {
          aiWsRef.current.send(event.data);
        }
        if (!DEBUG_INPUT_LEVEL) return;
        if (frameCount === 0) {
          console.log("[input] first frame from worklet — audio graph is running");
        }
        const samples = new Int16Array(event.data);
        for (let i = 0; i < samples.length; i++) {
          const level = Math.abs(samples[i]);
          if (level > peak) peak = level;
        }
        if (++frameCount % 50 === 0) {
          console.log(`[input] peak in last second: ${peak} / 32767`);
          silentSeconds = peak === 0 ? silentSeconds + 1 : 0;
          peak = 0;
          if (silentSeconds === 3) {
            console.warn(
              "[input] caller audio has been pure silence for 3s. The track " +
                "exists but no usable audio is flowing — check the [stats] " +
                "bytesReceived lines (0 = RTP never arrives: ICE/TURN/media relay)."
            );
          }
        }
      };

      aiSilentGainRef.current = audioContext.createGain();
      aiSilentGainRef.current.gain.value = 0;

      aiInputNodeRef.current.connect(aiInputWorkletRef.current);
      aiInputWorkletRef.current.connect(aiSilentGainRef.current);
      aiSilentGainRef.current.connect(audioContext.destination);

      connectAIWebSocket();
    },
    [connectAIWebSocket, ensurePCMWorklet, getAudioContext]
  );

  /* =========================================================
     TTS OUTPUT GRAPH
     WHY: backend returns raw 24kHz PCM chunks; we need a MediaStream the SIP
     PeerConnection can send + local speakers can play. One shared Destination
     + Gain lets enqueueTTS schedule gapless playback.
     WHAT: creates MediaStreamDestination + Gain(1.0) wired to destination and
     speakers; no-op if already created.
  ========================================================= */
  const initializeTTSOutput = useCallback(() => {
    const audioContext = aiAudioContextRef.current;
    if (!audioContext) {
      throw new Error("AudioContext is not ready");
    }
    if (aiOutputDestinationRef.current) return;
    aiOutputDestinationRef.current =
      audioContext.createMediaStreamDestination();
    aiOutputGainRef.current = audioContext.createGain();
    aiOutputGainRef.current.gain.value = 1;
    aiOutputGainRef.current.connect(aiOutputDestinationRef.current);
    aiOutputGainRef.current.connect(audioContext.destination);
  }, []);

  /* =========================================================
     SEND AI AUDIO INTO SIP
     WHY: caller must hear AI. Best path is replaceTrack (no renegotiation);
     addTrack+re-INVITE is fallback when mic permission was missing at accept().
     WHAT: grabs AI output track, finds SIP audio sender, replaceTrack or
     addTrack + session.invite().
  ========================================================= */
  const routeAIToSIP = useCallback(async () => {
    const pc = sipPeerConnectionRef.current;
    if (!pc) {
      console.error("SIP PeerConnection not available");
      return;
    }
    initializeTTSOutput();
    const aiStream = aiOutputDestinationRef.current!.stream;
    const aiTrack = aiStream.getAudioTracks()[0];
    if (!aiTrack) {
      console.error("AI output audio track not found");
      return;
    }
    const senders = pc.getSenders();
    sipAudioSenderRef.current =
      senders.find((sender) => sender.track && sender.track.kind === "audio") ??
      null;

    if (sipAudioSenderRef.current) {
      await sipAudioSenderRef.current.replaceTrack(aiTrack);
      console.log("AI audio track attached to SIP (replaceTrack, no renegotiation needed)");
    } else {
      sipAudioSenderRef.current = pc.addTrack(aiTrack, aiStream);
      console.warn(
        "No existing audio sender found — used addTrack, which requires " +
          "renegotiation. This usually means mic permission was not granted " +
          "before the call. Attempting a re-INVITE now."
      );
      try {
        const session = sipSessionRef.current;
        if (session && typeof session.invite === "function") {
          await session.invite();
          console.log("Renegotiation (re-INVITE) completed");
        } else {
          console.error(
            "No renegotiation method available on the session — the " +
              "remote party will NOT hear AI audio. Grant mic permission " +
              "before the call arrives to avoid this path entirely."
          );
        }
      } catch (error) {
        console.error("Renegotiation failed:", error);
      }
    }
  }, [initializeTTSOutput]);

  /* =========================================================
     QUEUE TTS PCM (synchronous — preserves chunk order)
     WHY: backend streams many small Int16 PCM chunks; must play in order
     without gaps/overlap. Odd byte counts (split frames) are carried over.
     WHAT: merge carry+new bytes -> Int16Array -> AudioBuffer at ttsRate ->
     BufferSource scheduled at aiOutputNextTime (now+0.05), tracked for stop.
  ========================================================= */
  function enqueueTTS(arrayBuffer: ArrayBuffer) {
    const audioContext = aiAudioContextRef.current;
    if (!audioContext) return;
    // initializeTTSOutput is stable; call via ref-safe inline logic
    if (!aiOutputDestinationRef.current || !aiOutputGainRef.current) {
      const dest = audioContext.createMediaStreamDestination();
      const gain = audioContext.createGain();
      gain.gain.value = 1;
      gain.connect(dest);
      gain.connect(audioContext.destination);
      aiOutputDestinationRef.current = dest;
      aiOutputGainRef.current = gain;
    }

    let bytes = new Uint8Array(arrayBuffer);
    if (carryRef.current.length) {
      const merged = new Uint8Array(carryRef.current.length + bytes.length);
      merged.set(carryRef.current);
      merged.set(bytes, carryRef.current.length);
      bytes = merged;
      carryRef.current = new Uint8Array(0);
    }
    const usable = bytes.length - (bytes.length % 2);
    if (usable < bytes.length) {
      carryRef.current = bytes.slice(usable);
    }
    if (!usable) return;

    const pcm = new Int16Array(
      bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + usable)
    );
    const buffer = audioContext.createBuffer(1, pcm.length, ttsRateRef.current);
    const channel = buffer.getChannelData(0);
    for (let i = 0; i < pcm.length; i++) {
      channel[i] = pcm[i] / 32768;
    }
    const source = audioContext.createBufferSource();
    source.buffer = buffer;
    source.connect(aiOutputGainRef.current!);

    const now = audioContext.currentTime;
    if (aiOutputNextTimeRef.current < now + 0.05) {
      aiOutputNextTimeRef.current = now + 0.05;
    }
    source.start(aiOutputNextTimeRef.current);
    aiOutputNextTimeRef.current += buffer.duration;
    aiOutputSourcesRef.current.push(source);
    source.onended = () => {
      aiOutputSourcesRef.current = aiOutputSourcesRef.current.filter(
        (item) => item !== source
      );
    };
  }

  /* =========================================================
     STOP TTS
     WHY: barge-in / interrupt / hangup must cut AI speech instantly, else
     stale audio overlaps next turn.
     WHAT: stops all scheduled BufferSources, clears queue/timer/carry.
  ========================================================= */
  function stopTTSPlayback() {
    aiOutputSourcesRef.current.forEach((source) => {
      try {
        source.stop();
      } catch {
        /* already stopped */
      }
    });
    aiOutputSourcesRef.current = [];
    aiOutputNextTimeRef.current = 0;
    carryRef.current = new Uint8Array(0);
  }

  /* =========================================================
     WAIT FOR SIP SESSION DESCRIPTION HANDLER
     WHY: sip.js creates peerConnection async after accept(); accessing it too
     early throws. Polling avoids race.
     WHAT: polls session.sessionDescriptionHandler up to timeoutMs, then returns it.
  ========================================================= */
  async function waitForSessionDescriptionHandler(
    session: any,
    timeoutMs = 10000
  ) {
    const startedAt = Date.now();
    while (!session.sessionDescriptionHandler) {
      if (Date.now() - startedAt >= timeoutMs) {
        throw new Error(
          "Session Description Handler was not created within the timeout."
        );
      }
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    return session.sessionDescriptionHandler;
  }

  /* =========================================================
     MEDIA DIAGNOSTICS
     WHY: most "AI hears nothing" bugs are SIP/ICE/RTP (no RTP, NAT, FreeSWITCH
     relay), not app code. Timers + getStats prove whether bytesReceived grows.
     WHAT: clearNoMediaTimer/armNoMediaTimer warns after 8s silence;
     logInboundAudioStats logs inbound-rtp bytes/packets at 3s/8s.
  ========================================================= */
  const clearNoMediaTimer = useCallback(() => {
    if (noMediaTimerRef.current) {
      clearTimeout(noMediaTimerRef.current);
      noMediaTimerRef.current = null;
    }
  }, []);

  const armNoMediaTimer = useCallback(() => {
    clearNoMediaTimer();
    noMediaTimerRef.current = setTimeout(() => {
      noMediaTimerRef.current = null;
      setStatus(
        "No caller audio received — check SIP/ICE media path (see console)",
        "error"
      );
      console.warn(
        `No caller RTP within ${NO_MEDIA_WARN_MS}ms of answering. ` +
          "This points to a SIP/ICE/media-relay issue (e.g. FreeSWITCH/Kamailio " +
          "not routing RTP to this browser leg, or ICE negotiation failing), " +
          "not an app-code issue."
      );
    }, NO_MEDIA_WARN_MS);
  }, [clearNoMediaTimer, setStatus]);

  async function logInboundAudioStats(pc: RTCPeerConnection, label: string) {
    if (pc !== sipPeerConnectionRef.current) return;
    try {
      const stats = await pc.getStats();
      stats.forEach((report) => {
        if (report.type === "inbound-rtp" && (report as any).kind === "audio") {
          const r = report as any;
          console.log(
            `[stats ${label}] inbound audio — bytes: ${r.bytesReceived}, ` +
              `packets: ${r.packetsReceived}`
          );
        }
      });
    } catch (error) {
      console.warn("getStats failed:", error);
    }
  }

  /* =========================================================
     CLEAN UP A CALL (or a pipeline test)
     WHY: prevents leaked WS/AudioNodes/mic tracks/ghost sessions across calls;
     callGenerationRef invalidates stale async startVoiceAI races.
     WHAT: bumps generation, closes AI WS, stops TTS, disconnects audio graph,
     stops test mic, clears SIP refs/audio element, resets transcript/UI state.
  ========================================================= */
  const cleanupCall = useCallback(() => {
    callGenerationRef.current++;
    clearNoMediaTimer();

    const socket = aiWsRef.current;
    aiWsRef.current = null;
    if (
      socket &&
      (socket.readyState === WebSocket.OPEN ||
        socket.readyState === WebSocket.CONNECTING)
    ) {
      try {
        socket.close(1000, "call ended");
      } catch {
        /* ignore */
      }
    }

    stopTTSPlayback();

    if (aiInputWorkletRef.current) {
      aiInputWorkletRef.current.port.onmessage = null;
    }
    [
      aiInputNodeRef.current,
      aiInputWorkletRef.current,
      aiSilentGainRef.current,
      aiOutputGainRef.current,
      aiOutputDestinationRef.current,
    ].forEach((node) => {
      if (!node) return;
      try {
        (node as AudioNode).disconnect();
      } catch {
        /* ignore */
      }
    });

    aiInputNodeRef.current = null;
    aiInputWorkletRef.current = null;
    aiSilentGainRef.current = null;
    aiOutputDestinationRef.current = null;
    aiOutputGainRef.current = null;
    aiOutputSourcesRef.current = [];
    aiOutputNextTimeRef.current = 0;
    carryRef.current = new Uint8Array(0);

    if (testMicStreamRef.current) {
      testMicStreamRef.current.getTracks().forEach((track) => track.stop());
      testMicStreamRef.current = null;
    }

    sipSessionRef.current = null;
    sipPeerConnectionRef.current = null;
    sipAudioSenderRef.current = null;
    if (remoteAudioRef.current) {
      remoteAudioRef.current.srcObject = null;
    }
    testModeActiveRef.current = false;

    setShowHangup(false);
    finalTextRef.current = "";
    answerTextRef.current = "";
    setTranscriptFinal("");
    setTranscriptInterim("");
    setAnswerText("");
    setAnswerPlaceholder(true);

    setIsRegistered((registered) => {
      if (registered) {
        setStatus("Registered — waiting for incoming call", "success", true);
      } else {
        setStatus("SIP not started");
      }
      return registered;
    });
  }, [clearNoMediaTimer, setStatus]);

  /* =========================================================
     ATTACH REMOTE SIP AUDIO
     WHY: Chrome only delivers remote WebRTC audio to WebAudio if also attached
     to <audio>; AI pipeline must start exactly once when first audio track appears.
     WHAT: waits for peerConnection, checks existing receivers else waits for
     'track' event; watchTrack arms no-media timer; beginAI attaches <audio>,
     routes AI to SIP, then startVoiceAI(stream). Guarded by started+generation.
  ========================================================= */
  const attachRemoteAudio = useCallback(
    async (session: any) => {
      console.log("Waiting for SIP SessionDescriptionHandler...");
      const generation = callGenerationRef.current;
      const handler = await waitForSessionDescriptionHandler(session);
      const pc = handler.peerConnection as RTCPeerConnection;
      if (!pc) {
        throw new Error("SIP PeerConnection is not available.");
      }
      sipPeerConnectionRef.current = pc;
      console.log("SIP PeerConnection:", pc);

      const existingSenders = pc.getSenders();
      console.log(
        "Senders immediately after accept():",
        existingSenders.map((s) => s.track && s.track.kind).filter(Boolean)
      );
      if (!existingSenders.some((s) => s.track && s.track.kind === "audio")) {
        console.warn(
          "No local audio sender present right after accept() — mic " +
            "getUserMedia likely did not succeed. AI audio will need a " +
            "renegotiation to reach the caller (see routeAIToSIP)."
        );
      }

      if (DEBUG_MEDIA_STATS) {
        setTimeout(() => logInboundAudioStats(pc, "3s"), 3000);
        setTimeout(() => logInboundAudioStats(pc, "8s"), 8000);
      }

      let started = false;

      const watchTrack = (track: MediaStreamTrack) => {
        clearNoMediaTimer();
        if (!track.muted) return;
        armNoMediaTimer();
        track.addEventListener("unmute", clearNoMediaTimer, { once: true });
      };

      const beginAI = async (stream: MediaStream) => {
        if (started) return;
        started = true;
        if (generation !== callGenerationRef.current) return;
        console.log("Caller audio track available — starting AI pipeline");

        if (remoteAudioRef.current) {
          remoteAudioRef.current.srcObject = stream;
          remoteAudioRef.current.play().catch((error) => {
            console.warn("Remote audio playback blocked:", error);
          });
        }

        try {
          await getAudioContext();
          if (generation !== callGenerationRef.current) return;
          initializeTTSOutput();
          await routeAIToSIP();
        } catch (error) {
          console.error("Failed to route AI audio into SIP:", error);
        }
        if (generation !== callGenerationRef.current) return;
        await startVoiceAI(stream);
      };

      const existingTracks = pc
        .getReceivers()
        .map((receiver) => receiver.track)
        .filter(
          (track): track is MediaStreamTrack =>
            !!track && track.kind === "audio"
        );

      if (existingTracks.length) {
        console.log("Remote audio track already present after accept()");
        watchTrack(existingTracks[0]);
        await beginAI(new MediaStream(existingTracks));
        return pc;
      }

      console.log("No remote audio track yet — waiting for track event");
      pc.addEventListener("track", (event: RTCTrackEvent) => {
        if (event.track.kind !== "audio") return;
        console.log("Incoming SIP audio track received");
        watchTrack(event.track);
        const stream =
          event.streams && event.streams.length
            ? event.streams[0]
            : new MediaStream([event.track]);
        beginAI(stream).catch((error) => {
          console.error("Failed to start AI pipeline:", error);
          setStatus("Failed to start AI pipeline: " + (error as Error).message, "error");
        });
      });

      armNoMediaTimer();
      return pc;
    },
    [
      armNoMediaTimer,
      clearNoMediaTimer,
      getAudioContext,
      initializeTTSOutput,
      routeAIToSIP,
      setStatus,
      startVoiceAI,
    ]
  );

  /* =========================================================
     MIC PERMISSION PRE-CHECK
     WHY: SIP.js needs a local audio sender at accept() time; without mic grant,
     replaceTrack path fails and renegotiation is required (often no audio).
     WHAT: one-shot getUserMedia({audio:true}) then immediate stop; returns
     false + error status if denied.
  ========================================================= */
  const ensureMicPermission = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      stream.getTracks().forEach((track) => track.stop());
      console.log("Microphone permission confirmed");
      return true;
    } catch (error) {
      console.error("Microphone permission check failed:", error);
      setStatus(
        "Microphone permission denied — incoming calls will not get AI audio. " +
          "Allow microphone access and reload.",
        "error"
      );
      return false;
    }
  }, [setStatus]);

  /* =========================================================
     TEST AI AUDIO (NO CALL)
     WHY: lets you verify mic -> Deepgram -> Groq -> Deepgram TTS without a SIP
     trunk; uses same worklet/WS path as real call.
     WHAT: guards busy session, gets context, inits TTS output, getUserMedia mic
     with echoCancellation/noiseSuppression, then startVoiceAI(micStream).
  ========================================================= */
  const testAIPipeline = useCallback(async () => {
    if (aiWsRef.current || sipSessionRef.current) {
      console.log("A voice session is already active — hang up first.");
      return;
    }
    testModeActiveRef.current = true;
    setShowHangup(true);
    setTranscriptFinal("(no call — speak into your microphone)");
    setTranscriptInterim("");
    setAnswerText("");
    setAnswerPlaceholder(true);
    setStatus("Testing with your microphone (no call)...", "", true);

    try {
      await getAudioContext();
      initializeTTSOutput();
      testMicStreamRef.current = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      console.log(
        "[test] microphone acquired — sending it through the same " +
          "worklet -> /ws/voice -> Deepgram path a call uses"
      );
      await startVoiceAI(testMicStreamRef.current);
    } catch (error) {
      console.error("Test pipeline failed:", error);
      cleanupCall();
      setStatus("Test failed: " + (error as Error).message, "error");
    }
  }, [cleanupCall, getAudioContext, initializeTTSOutput, setStatus, startVoiceAI]);

  /* =========================================================
     INITIALIZE SIP USER AGENT
     WHY: registers extension on SIP server (Kamailio/FreeSWITCH) over WSS so
     incoming INVITEs route here; onInvite auto-answers exactly one call.
     WHAT: checks mic, builds sip:user@domain URI, new UserAgent({transport,
     STUN, audio constraints}), sets delegate.onInvite (busy->486, else accept
     + attachRemoteAudio), userAgent.start() + Registerer.register().
  ========================================================= */
  const initializeSIP = useCallback(async () => {
    try {
      console.log("SIP.js module is available.");

      const micOk = await ensureMicPermission();
      if (!micOk) return;

      setStatus("Starting SIP...");

      const { domain, username, password } = credsRef.current;
      const uri = UserAgent.makeURI(`sip:${username}@${domain}`);
      if (!uri) {
        throw new Error("Invalid SIP URI");
      }

      const userAgent = new UserAgent({
        uri,
        transportOptions: {
          server: SIP_WS,
          traceSip: true,
        },
        authorizationUsername: username,
        authorizationPassword: password,
        sessionDescriptionHandlerFactoryOptions: {
          constraints: { audio: true, video: false },
          peerConnectionConfiguration: {
            iceServers: [{ urls: "stun:stun.l.google.com:19302" }],
          },
        },
      } as any);
      userAgentRef.current = userAgent;

      userAgent.delegate = {
        onInvite: async (invitation: any) => {
          console.log("================================");
          console.log("INCOMING SIP CALL");
          console.log("================================");

          if (sipSessionRef.current || testModeActiveRef.current) {
            console.warn("Busy — rejecting second incoming call");
            try {
              await invitation.reject({ statusCode: 486 });
            } catch (error) {
              console.error("Failed to reject second call:", error);
            }
            return;
          }

          try {
            sipSessionRef.current = invitation;

            invitation.stateChange.addListener((state: string) => {
              console.log("SIP session state:", state);
              if (
                state === SessionState.Terminated &&
                sipSessionRef.current === invitation
              ) {
                cleanupCall();
              }
            });

            setShowHangup(true);
            setStatus("Incoming call — answering...", "", true);

            await invitation.accept({
              sessionDescriptionHandlerOptions: {
                constraints: { audio: true, video: false },
              },
            });
            console.log("SIP call answered");

            await attachRemoteAudio(invitation);
            console.log("SIP remote audio handler attached");

            if (!aiWsRef.current && sipSessionRef.current === invitation) {
              setStatus(
                "Call answered — waiting for caller audio",
                "success",
                true
              );
            }
          } catch (error) {
            console.error("Failed to answer SIP call:", error);
            setStatus("Failed to answer call", "error");
          }
        },
      };

      await userAgent.start();
      console.log("SIP UserAgent started");

      const registerer = new Registerer(userAgent as any);
      registererRef.current = registerer;
      await registerer.register();
      console.log("SIP registration successful");

      setIsRegistered(true);
      setStatus("Registered — waiting for incoming call", "success", true);
    } catch (error) {
      console.error("SIP initialization error:", error);
      setStatus("SIP registration failed: " + (error as Error).message, "error");
      setIsStarting(false);
    }
  }, [attachRemoteAudio, cleanupCall, ensureMicPermission, setStatus]);

  /* =========================================================
     HANG UP SIP CALL / STOP TEST
     WHY: Established calls need BYE, early (Initial) need reject(); test mode
     has no SIP session so just cleanup.
     WHAT: bye()/reject() if session exists, then cleanupCall() always.
  ========================================================= */
  const hangUpSIP = useCallback(async () => {
    const session = sipSessionRef.current;
    try {
      if (session) {
        if (session.state === SessionState.Established) {
          await session.bye();
        } else if (
          session.state === SessionState.Initial &&
          typeof session.reject === "function"
        ) {
          await session.reject();
        }
      }
    } catch (error) {
      console.error("Hangup error:", error);
    }
    cleanupCall();
  }, [cleanupCall]);

  /* =========================================================
     DISCONNECT SIP (hang up, unregister, stop agent)
     WHY: full teardown: stop media, tell server to stop routing (unregister),
     drop WSS transport; else stale registration keeps ringing this browser.
     WHAT: bye/reject active call -> cleanupCall -> registerer.unregister() ->
     userAgent.stop() -> reset isRegistered/isStarting flags.
  ========================================================= */
  const handleDisconnect = useCallback(async () => {
    if (!userAgentRef.current && !isRegistered && !isStarting) return;

    setIsDisconnecting(true);
    setStatus("Disconnecting SIP...");

    /* End any active call or mic test first. */
    try {
      const session = sipSessionRef.current;
      if (session) {
        if (session.state === SessionState.Established) {
          await session.bye();
        } else if (
          session.state === SessionState.Initial &&
          typeof session.reject === "function"
        ) {
          await session.reject();
        }
      }
    } catch (error) {
      console.error("Hangup during disconnect failed:", error);
    }
    cleanupCall();

    /* Unregister the extension so the server stops routing calls here. */
    if (registererRef.current) {
      try {
        await registererRef.current.unregister();
        console.log("SIP unregistered");
      } catch (error) {
        console.error("SIP unregister failed:", error);
      }
      registererRef.current = null;
    }

    /* Stop the agent and drop the WebSocket transport. */
    if (userAgentRef.current) {
      try {
        userAgentRef.current.delegate = undefined;
        await userAgentRef.current.stop();
        console.log("SIP UserAgent stopped");
      } catch (error) {
        console.error("SIP UserAgent stop failed:", error);
      }
      userAgentRef.current = null;
    }

    setIsRegistered(false);
    setIsStarting(false);
    setIsDisconnecting(false);
    setStatus("SIP disconnected");
  }, [cleanupCall, isRegistered, isStarting, setStatus]);

  /* =========================================================
     BUTTONS
     WHY: Start button captures creds into ref (avoids stale closure in
     initializeSIP), warms AudioContext inside click gesture (autoplay policy),
     then registers.
     WHAT: handleRegister validates domain/user/pass -> credsRef -> getAudioContext
     -> initializeSIP; useEffect re-enables button if registration failed.
  ========================================================= */
  const handleRegister = useCallback(async () => {
    const domain = sipDomain.trim();
    const username = sipUsername.trim();
    const password = sipPassword;
    if (!domain || !username || !password) {
      setStatus("Please enter SIP domain, username, and password.", "error");
      return;
    }
    credsRef.current = { domain, username, password };
    setIsStarting(true);
    setStatus("Validating SIP credentials...");
    try {
      await getAudioContext();
    } catch (error) {
      console.warn("Could not create AudioContext yet:", error);
    }
    await initializeSIP();
    setIsStarting((starting) => {
      // initializeSIP sets isRegistered on success; re-enable on failure.
      return false;
    });
    void 0;
  }, [
    sipDomain,
    sipUsername,
    sipPassword,
    getAudioContext,
    initializeSIP,
    setStatus,
  ]);

  // Re-enable the Start button if registration failed.
  useEffect(() => {
    if (!isRegistered) setIsStarting(false);
  }, [isRegistered]);

  /* =========================================================
     PAGE CLEANUP (replaces window beforeunload)
     WHY: closing/reload must send SIP BYE/unregister + close AI WS, else server
     holds ghost registration and backend VoiceSession leaks.
     WHAT: beforeunload listener closes WS, bye() active call, unregister,
     userAgent.stop(); same handler runs on React unmount.
  ========================================================= */
  useEffect(() => {
    const handleBeforeUnload = () => {
      if (aiWsRef.current && aiWsRef.current.readyState === WebSocket.OPEN) {
        aiWsRef.current.close();
      }
      const session = sipSessionRef.current;
      if (session && session.state === SessionState.Established) {
        try {
          session.bye();
        } catch {
          /* ignore */
        }
      }
      if (registererRef.current) {
        try {
          registererRef.current.unregister();
        } catch {
          /* ignore */
        }
      }
      if (userAgentRef.current) {
        try {
          userAgentRef.current.stop();
        } catch {
          /* ignore */
        }
      }
    };
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => {
      window.removeEventListener("beforeunload", handleBeforeUnload);
      handleBeforeUnload();
    };
  }, []);

  const transcriptEmpty = !transcriptFinal && !transcriptInterim;

  return (
    <div className="card">
      <h1>Voice AI</h1>

      <div className="description">
        Incoming SIP calls are automatically answered by AI.
      </div>

      <div className="sip-credentials">
        <div className="input-group">
          <label htmlFor="sipDomain">SIP Domain</label>
          <input
            type="text"
            id="sipDomain"
            placeholder="cl003lc001.com"
            autoComplete="on"
            value={sipDomain}
            onChange={(e) => setSipDomain(e.target.value)}
          />
        </div>

        <div className="input-group">
          <label htmlFor="sipUsername">SIP Username</label>
          <input
            type="text"
            id="sipUsername"
            placeholder="1001"  
            autoComplete="username"
            value={sipUsername}
            onChange={(e) => setSipUsername(e.target.value)}
          />
        </div>

        <div className="input-group">
          <label htmlFor="sipPassword">SIP Password</label>
          <input
            type="password"
            id="sipPassword"
            placeholder="7e15842a"
            autoComplete="current-password"
            value={sipPassword}
            onChange={(e) => setSipPassword(e.target.value)}
          />
        </div>
      </div>

      <button
        className="sip-register-btn"
        onClick={handleRegister}
        disabled={isRegistered || isStarting || isDisconnecting}
      >
        {isRegistered ? "SIP Registered" : "Start SIP"}
      </button>

      {(isRegistered || isStarting) && (
        <button
          className="disconnect-btn"
          onClick={handleDisconnect}
          disabled={isDisconnecting}
        >
          {isDisconnecting ? "Disconnecting..." : "Disconnect SIP"}
        </button>
      )}

      <button className="test-btn" onClick={testAIPipeline}>
        Test with Microphone (no call)
      </button>

      {showHangup && (
        <button className="hangup-btn" onClick={hangUpSIP}>
          Hang Up / Stop Test
        </button>
      )}

      {/*
        Caller audio is attached here for two reasons: local debugging, and
        because Chrome only delivers remote WebRTC audio to Web Audio nodes
        when the stream is also attached to a playing media element.
        The AI's audio is routed directly into the SIP PeerConnection.
      */}
      <audio ref={remoteAudioRef} className="remote-audio" autoPlay playsInline />

      <div className="section">
        <div className="label">Caller</div>
        <div className="box">
          {transcriptEmpty ? (
            "Waiting for caller..."
          ) : (
            <>
              {transcriptFinal}{" "}
              {transcriptInterim && (
                <span className="interim">{transcriptInterim}</span>
              )}
            </>
          )}
        </div>
      </div>

      <div className="section">
        <div className="label">AI Assistant</div>
        <div className="box">
          {answerPlaceholder && !answerText
            ? "AI response will appear here..."
            : answerText}
        </div>
      </div>

      <div className="sip-info">
        SIP extension: <strong>1001</strong>
        <br />
        Waiting for incoming calls...
      </div>

      <div className={`status ${status.type}`}>
        {status.live && <span className="pulse" />}
        {status.text}
      </div>
    </div>
  );
}
