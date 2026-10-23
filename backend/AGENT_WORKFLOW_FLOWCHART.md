# Multi-Agent Voice Workflow Flowchart

```mermaid
flowchart TD
    A([Start: caller speaks]) --> B[Frontend sends audio over /ws/voice]
    B --> C[voice_socket in voice_controller.py]
    C --> D{Deepgram configured and connected?}
    D -- No --> E[Close WebSocket with error]
    D -- Yes --> F[RealtimeVoiceSession.pump_client forwards audio]
    F --> G[RealtimeVoiceSession.pump_deepgram receives transcript]
    G --> H{Final transcript available?}
    H -- No --> G
    H -- Yes --> I[RealtimeVoiceSession.start_turn]
    I --> J[RealtimeVoiceSession.run: workflow.stream]
    J --> K{RegexInjectionGuardrail.is_allowed?}
    K -- No --> L[Yield safe dental-clinic reply]
    K -- Yes --> M[LangGraph: ConversationWorkflow._respond]

    M --> N{route_to_agent: classify request}
    N -- Dental/clinic question --> O[Assign dental_care agent]
    N -- Appointment/booking --> P[Assign appointments agent]
    N -- Patient record --> Q[Assign patient_records agent]
    O --> R[messages_for_agent: instructions and tool scope]
    P --> R
    Q --> R
    R --> S[Bind allowed tools and call Groq]
    S --> T{Model response has tool calls?}

    T -- No --> U[ConversationWorkflow._record_turn]
    T -- Yes --> V[ConversationWorkflow._tools]
    V --> W{Escalation to human?}
    W -- Yes --> X[Set human_handoff=hangup]
    X --> Y[Yield HANGUP_CALL]
    Y --> Z[RealtimeVoiceSession announces goodbye and closes call]
    W -- No --> AA{Tool allowed for assigned agent?}
    AA -- No --> AB[Block tool; return scope error to model]
    AA -- Yes --> AC{Booking request?}
    AC -- Yes --> AD{Required details and explicit confirmation present?}
    AD -- No --> AE[Block booking; ask caller for details/confirmation]
    AD -- Yes --> AF{Tool already used or limit reached?}
    AC -- No --> AF
    AF -- Yes --> AG[Block duplicate/extra call; return error]
    AF -- No --> AH{Patient lookup phone valid: 10-15 digits?}
    AH -- No --> AI[Block lookup; ask for a real phone number]
    AH -- Yes --> AJ[LlmToolCallExecutor.execute: run tool call]

    AJ --> AK{Which tool?}
    AK -- book_appointment --> AL[appointment.py → portal_repository.py]
    AK -- patient/services/providers/slots --> AM[portal_tools.py]
    AL --> AN[CustomerPortalHttpClient.request → Customer Portal API]
    AM --> AN
    AN --> AO{Tool/API succeeded?}
    AO -- No --> AP[Return tool failure/result to specialist]
    AO -- Yes --> AQ[Return tool result to specialist]
    AB --> AR[Return error to specialist]
    AE --> AR
    AG --> AR
    AI --> AR
    AP --> AS[Same specialist continues with Groq]
    AQ --> AS
    AR --> AS
    AS --> T

    U --> AT[ConversationWorkflow.stream yields response text]
    L --> AT
    AT --> AU[RealtimeVoiceSession publishes text and chunks it]
    AU --> AV[SentenceChunkSplitter.split]
    AV --> AW[RealtimeVoiceSession._speak → TTS converts text to audio]
    AW --> AX{TTS succeeds?}
    AX -- No --> AY[Handle/log TTS error; audio may not be delivered]
    AX -- Yes --> AZ[Send audio and response events to frontend]
    AZ --> BA([End turn])
    AY --> BA
```

## Parallel versus sequential execution

- A single turn is sequential: audio → transcript → agent assignment → model →
  optional tool loop → final response → TTS.
- One specialist handles a turn; tool follow-up stays with that specialist.
- Specialists are **not** run in parallel. `LlmToolCallExecutor.execute_tool_calls()` loops over
  tool calls one at a time. Separate voice sessions can run concurrently.

## Function and file index

| Function | File | What it does |
|---|---|---|
| `voice_socket(ws)` | `modules/realtime/voice_controller.py` | Accepts `/ws/voice`, checks Deepgram settings, connects STT and constructs `RealtimeVoiceSession`. |
| `RealtimeVoiceSession.pump_client()` | `modules/realtime/session.py` | Forwards browser audio to Deepgram; handles config and interrupt messages. |
| `RealtimeVoiceSession.pump_deepgram()` | `modules/realtime/session.py` | Processes Deepgram events/transcripts and starts turns. |
| `RealtimeVoiceSession.start_turn()` | `modules/realtime/session.py` | Takes buffered transcript and schedules a response. |
| `RealtimeVoiceSession.run(text)` | `modules/realtime/session.py` | Calls the workflow, streams response events, chunks speech, invokes TTS and updates history. |
| `ConversationWorkflow.stream(text, history)` | `modules/workflow/graph.py` | Runs guardrail and LangGraph; yields response text or control markers. |
| `RegexInjectionGuardrail.is_allowed(text)` | `modules/llm/guard_rail.py` | Allows or rejects user input before model processing. |
| `route_to_agent(messages)` | `modules/workflow/agents.py` | Selects `dental_care`, `appointments`, or `patient_records`. |
| `messages_for_agent(...)` | `modules/workflow/agents.py` | Creates specialist prompt with assigned task and permitted tools. |
| `ConversationWorkflow._respond(state)` | `modules/workflow/graph.py` | Calls the model using the selected specialist prompt and scoped tools. |
| `ConversationWorkflow._route_after_response(state)` | `modules/workflow/graph.py` | Sends tool requests to `_tools`; sends final replies to `_record_turn`. |
| `ConversationWorkflow._tools(state)` | `modules/workflow/graph.py` | Handles escalation and checks tool scope, booking confirmation, phone format, duplicates and limits. |
| `LlmToolCallExecutor.execute_tool_calls(response, messages)` | `modules/llm/tool_executor.py` | Runs approved calls sequentially and appends tool results. |
| `LlmToolCallExecutor._validate_and_execute_booking(...)` | `modules/llm/tool_executor.py` | Checks booking fields and explicit confirmation before booking. |
| `book_appointment(...)` / `create_appointment_booking_handler(...)` | `modules/tools/appointment.py` | Validates and delegates booking to the repository. |
| `find_patient_by_phone(...)`, `list_services(...)`, `list_providers(...)`, `find_available_slots(...)` | `modules/tools/portal_tools.py` | Retrieve patient and clinic appointment data. |
| `CustomerPortalHttpClient.request(...)` | `modules/portal/client.py` | Makes authenticated requests to the customer portal API. |
| `ConversationWorkflow._record_turn(state)` | `modules/workflow/graph.py` | Extracts final response text or returns the hangup marker. |
| `RealtimeVoiceSession._speak(text, elapsed)` | `modules/realtime/session.py` | Sends response text to TTS and streams generated audio. |
| `SentenceChunkSplitter.split(...)` | `modules/llm/chunking.py` | Splits response text into speech-ready chunks. |
