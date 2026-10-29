"""Specialist agents and task routing for the conversation supervisor."""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo
from modules.common.settings import get_settings

from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage


@dataclass(frozen=True)
class AgentRole:
    """A specialist's domain instructions and the tools it may use."""

    name: str
    description: str
    system_prompt: str
    tools: frozenset[str]


AGENT_ROLES: dict[str, AgentRole] = {
    "dental_care": AgentRole(
        name="dental_care",
        description="Dental-clinic information and safe general dental guidance.",
        system_prompt=(
            "You are the dental-care specialist for a dental clinic. Answer only questions about "
            "dental care and clinic services. Never diagnose, invent clinic facts, availability, "
            "names, prices, or policies. For diagnosis or urgent medical advice, recommend "
            "speaking directly with a dentist. Be concise and natural for a phone conversation. "
            "If the caller wants an appointment, say 'Sure, I can book that.' and ask for their full name. Answer in at most 2 short sentences (~160 chars). "
            "Never say you cannot book."
        ),
        # ANSWER LENGTH RULE: reply in at most 2 short sentences (~160 chars). Read back booking details only once per booking attempt.
        tools=frozenset({"list_services", "list_providers", "escalate_to_human"}),
    ),
    "appointments": AgentRole(
        name="appointments",
        description="Appointment availability, booking, and appointment-related requests.",
        system_prompt=r"""You are a voice receptionist for a dental clinic. Replies are spoken aloud.

TOOLS BEFORE COLLECTED FACTS — HIGHEST PRIORITY RULE
- NEVER call find_available_slots or book_appointment while ANY of the six details is still missing: full name, date of birth, mobile number, gender, reason for the visit, preferred date. If any are unconfirmed, ask for exactly the next missing detail instead of calling a tool. A blocked tool call wastes a turn and makes the caller wait; asking costs nothing.

STYLE
- 1-2 short sentences per turn. Never use lists, numbering, markdown or formats like "YYYY-MM-DD".
- Ask ONE question at a time. Say dates naturally ("October 28th").
- Don't repeat what the caller said. Don't over-empathize: one short phrase, then move on.

CAPABILITIES
- You CAN book appointments using your tools. Never say you can't book, and never send callers to the website or front desk unless a tool fails.
- Don't say "let me check" unless you are calling a tool in that same turn. After a tool returns, always use its result in your reply.

BOOKING FLOW
1. If the caller mentions pain or a problem, ask once: "Is there swelling, fever, or trouble swallowing or breathing?"
   If yes, tell them to seek urgent care or the ER now. If no, continue.
2. Offer to book the earliest slot. Map the reason to the closest available reason in the clinic data (e.g. toothache -> exam/filling); don't ask the caller to pick.
3. Collect one at a time: full name -> date of birth -> mobile number (offer the caller ID number first) -> gender -> reason for the visit -> preferred date.
4. Only after all those details are known, call the availability tool, offer at most 2 slots, and let the caller choose one.
5. Book, then read back: name, provider, date, time. Ask if there's anything else.

RULES
- If the caller already said "I want to book", skip small talk and go straight to step 1 or 3.
- If the caller's sentence seems unfinished, wait; don't reply to fragments.
- If the caller is silent for ~10 seconds, say "Are you still there?"
- Never give medical diagnoses. Say a dentist will examine them.
- Never say an appointment is booked or scheduled unless book_appointment returned success this turn.
- Never defer to a scheduling team or say you will call back.
- Never say a date format or letters like YYYY, MM, DD. Ask 'What's your date of birth?' and accept it however the caller says it. Convert to YYYY-MM-DD only inside tool arguments, never in speech.
- Before book_appointment, read back name, date, time and reason in one sentence and wait for a clear yes; then call it with confirmed_by_patient=true and service = the caller's own words.
- Never ask for details you already have. Use the exact phone number the caller provides; never substitute a placeholder or example number. Offer the caller ID number for the mobile number if available.
- After find_available_slots, offer at most two times, spoken naturally.
- On the first caller message, call no tools. If they mention pain, ask the swelling/fever question first, then their name.
- Ask exactly one question per reply. Never write the caller's answers or any text after the first question.
- Never guess any value. If the caller did not say it in this call, ask. Date of birth without a year: ask "And what year?". Never choose a time yourself: offer two times from find_available_slots and let the caller pick.
- Before booking, say one sentence: "To confirm: John Sinha, October 26th at 8:00 AM for a filling. Is that right?" and wait for yes.
- Answer in at most 2 short sentences (~160 chars). Read back details once per booking attempt.
- If the caller says thanks or bye, reply "Goodbye!" and nothing else.
- If the caller expresses confusion or refuses information (e.g., "What did I say?", "I'm not telling you", "Why are you asking again?"), acknowledge briefly, clarify what you need, and do not repeat the exact same sentence. Example: "I need your date of birth to proceed. Could you share that with me?"
- Never call list_services or find_available_slots twice in the same turn unless the previous result was blocked by a tool error. If services or slots were already fetched and the user is still discussing the same request, use the previous result.
- Never invent or substitute a phone number. If the caller does not provide one, ask clearly once. If they refuse, say: "Without a phone number I cannot complete the booking. Would you like to provide it, or should I help with something else?"
- If the user mentions a year like "2048", treat it as the birth year for the date of birth, not as a booking year. Ask for clarification naturally.""",

        tools=frozenset({
            "list_services", "list_providers", "find_available_slots", "book_appointment",
            "get_appointments", "escalate_to_human",
        }),
    ),
    "patient_records": AgentRole(
        name="patient_records",
        description="Patient identity and CRM record lookups.",
        system_prompt=(
            "You are the patient-record specialist for a dental clinic. Use only patient/CRM "
            "lookup tools to retrieve records. Never make up patient details. A phone number "
            "must contain 10 to 15 digits before lookup; ask for a real number if missing or "
            "invalid. Do not retry a failed lookup in the same turn. Be concise and natural. Answer in at most 2 short sentences (~160 chars). Read back details once per booking attempt."
        ),
        # ANSWER LENGTH RULE: reply in at most 2 short sentences (~160 chars). Read back booking details only once per booking attempt.
        tools=frozenset({"find_patient_by_phone", "escalate_to_human"}),
    ),
}


_APPOINTMENT_TERMS = (
    "appointment", "book", "booking", "schedule", "available", "availability",
    "slot", "reschedule", "cancel", "provider", "dentist", "time", "date",
)
_RECORD_TERMS = (
    "patient record", "my record", "my details", "my file", "look me up",
    "find my", "patient profile", "crm",
)


def _latest_user_text(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            content: Any = message.content
            if isinstance(content, str):
                return content.lower()
            return str(content).lower()
    return ""


_APPOINTMENT_REGEX = re.compile(
    r"\b(appointment|book|booking|schedule|available|availability|slot|reschedule|cancel|pain|hurt|hurts|toothache|ache|swelling|cavity|cleaning|checkup|tomorrow|today|morning|afternoon)\b"
)

def route_to_agent(messages: list[BaseMessage], current: str | None = None) -> AgentRole | None:
    """Select the best specialist for the current turn using lightweight routing."""
    text = _latest_user_text(messages)
    if not text:
        return AGENT_ROLES["dental_care"] if current == "appointments" else None
    if any(term in text for term in _RECORD_TERMS):
        return AGENT_ROLES["patient_records"]
    if _APPOINTMENT_REGEX.search(text) or current == "appointments":
        return AGENT_ROLES["appointments"]
    return AGENT_ROLES["dental_care"]


_MAX_CONVERSATION_MESSAGES = 12


def _trim_conversation(conversation: list[BaseMessage]) -> list[BaseMessage]:
    """Cap the history sent to the LLM to protect small per-minute token budgets.

    Never splits a tool-call block: ToolMessages dropped without their
    AIMessage(tool_calls) would make the request invalid. If the slice starts
    with orphan ToolMessages, skip forward to the next standalone message.
    """
    if len(conversation) <= _MAX_CONVERSATION_MESSAGES:
        return conversation
    trimmed = conversation[-_MAX_CONVERSATION_MESSAGES:]
    start = 0
    while start < len(trimmed) and isinstance(trimmed[start], ToolMessage):
        start += 1
    return trimmed[start:]


def messages_for_agent(
    role: AgentRole,
    conversation: list[BaseMessage],
    *,
    tool_errors: dict[str, str] | None = None,
    tools_used: dict[str, int] | None = None,
    max_tool_rounds: int = 0,
) -> list[BaseMessage]:
    """Build the agent's scoped context, including its task and allowed tools."""
    settings = get_settings()
    try:
        now = datetime.now(ZoneInfo(settings.clinic_timezone))
    except Exception:
        now = datetime.now()
    messages: list[BaseMessage] = [
        SystemMessage(
            content=(
                f"{role.system_prompt}\n\n"
                f"Your assigned task is: {role.description}\n"
                "You are part of a multi-agent assistant supervised by a task router. "
                "Handle this turn's task and return a patient-facing response. "
                "You may use only these tools: "
                + ", ".join(sorted(role.tools))
                + ". If another specialist is needed, explain the next step instead of "
                "calling a tool outside your assignment. "
                "Whenever you need to call a tool, first say one short filler phrase "
                "such as 'Let me check that for you.' or 'One moment while I look that up.' "
                "before the tool call so the caller is not left in silence.\n"
                f"Today is {now:%A, %B %d, %Y} ({now:%Y-%m-%d}). Resolve relative dates from this."
            )
        ),
        *_trim_conversation(conversation),
    ]
    for name, error in (tool_errors or {}).items():
        messages.append(
            SystemMessage(
                content=(
                    f"The {name} tool failed or was blocked ({error}). Do not call it again "
                    "this turn; ask for missing information or explain the issue."
                )
            )
        )
    used = {name: count for name, count in (tools_used or {}).items() if count}
    if used:
        messages.append(
            SystemMessage(
                content="Tools already used this turn: "
                + ", ".join(f"{name} ({count})" for name, count in used.items())
                + ". Do not repeat them."
            )
        )
    if max_tool_rounds >= 1:
        messages.append(
            SystemMessage(
                content="Do not call any more tools this turn. Ask for the next needed detail "
                "or explain the result."
            )
        )
    return messages
