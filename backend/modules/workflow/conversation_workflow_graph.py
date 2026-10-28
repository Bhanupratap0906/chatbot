"""LangGraph workflow for tool use, CRM lookups, booking, and human-call hangup."""

from __future__ import annotations

import logging

import uuid
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from modules.common.conversation_cost_tracker import ConversationCostTracker
from modules.common.settings import get_settings
from modules.llm.llm_error_classification import is_tool_validation_error
from modules.llm.prompt_injection_guardrail import RegexInjectionGuardrail
from modules.tools import TOOL_REGISTRY
from modules.llm.llm_tool_call_executor import LlmToolCallExecutor
logger = logging.getLogger("workflow.graph")


def _normalize_date_str(date_str: str) -> datetime.date | None:
    import re as _re
    from datetime import datetime
    text = (date_str or "").strip()
    text = _re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", text, flags=_re.IGNORECASE)
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d-%m-%Y", "%B %d %Y", "%b %d %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _normalize_time_str(time_str: str) -> str | None:
    from datetime import datetime
    text = (time_str or "").strip().upper()
    for fmt in ("%I:%M %p", "%I:%M%p", "%H:%M"):
        try:
            return datetime.strptime(text, fmt).strftime("%H:%M")
        except ValueError:
            continue
    return None


def _normalize_phone_str(phone_str: str) -> str:
    import re
    return re.sub(r"\D", "", str(phone_str or ""))

from modules.workflow.conversation_agents import AgentRole, AGENT_ROLES, messages_for_agent, route_to_agent


@tool
async def escalate_to_human(reason: str) -> str:
    """Hang up the current call when the caller requests a person or needs human help.

    Args:
        reason: Brief reason for the requested human assistance.
    """
    return f"Hang up call for human assistance: {reason}"


class WorkflowState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    turn_text: str
    turn_id: int
    response_text: str
    workflow: dict[str, Any]
    tool_calls_by_name: dict[str, int]
    tool_errors: dict[str, str]
    max_tool_rounds: int
    assigned_agent: str
    pending_booking: dict | None
    handoff_requested: bool


class ConversationWorkflow:
    """Conversation-scoped LangGraph with a checkpointed workflow and tool loop."""

    def __init__(
        self,
        llm=None,
        cost_tracker: ConversationCostTracker | None = None,
        thread_id: str | None = None,
    ):
        settings = get_settings()
        self.model = settings.groq_model
        self.thread_id = thread_id or str(uuid.uuid4())
        if llm is None:
            from langchain_groq import ChatGroq

            llm = ChatGroq(
                model=self.model,
                temperature=0,
                max_tokens=settings.llm_max_tokens,
                max_retries=2,
                timeout=30,
                reasoning_format="hidden",
                reasoning_effort="low",
            )
        self.llm = llm
        self.guardrail = RegexInjectionGuardrail()
        self.tools = [*TOOL_REGISTRY, escalate_to_human]
        self.tool_executor = LlmToolCallExecutor(self.tools)
        self.llm_with_tools = self.llm.bind_tools(self.tool_executor.tools)
        self.cost_tracker = cost_tracker
        if cost_tracker is not None:
            from langchain_groq import ChatGroq
            from modules.common.usage_capturing_async_client import _UsageCapturingAsyncClient

            if isinstance(self.llm, ChatGroq):
                original_client = self.llm.async_client
                object.__setattr__(
                    self.llm,
                    "async_client",
                    _UsageCapturingAsyncClient(
                        original_client,
                        lambda usage: cost_tracker.add_llm_usage(usage, self.model),
                    ),
                )
        self._saver = _get_or_create_thread_checkpointer(self.thread_id)
        self._graph = self._build_graph()
        self.hangup_requested = False
        self._current_turn_hangup = False
        self._pending_booking = None
        self._booked_in_conversation = False
        self._booked_this_turn = False
        # Conversation-level booking tracking stays (item 4)
        self._booking_confirmation = ""
        self._active_agent = ""
        self._pending_booking: dict | None = None
        self._booked_in_conversation = False
        self._turn_id = 0
        self.history_messages: list[BaseMessage] = []
        self.workflow_state: dict[str, Any] = {}
        self.tool_calls_by_name: dict[str, int] = {}
        self.tool_errors: dict[str, str] = {}
        self.max_tool_rounds = 0
        self._current_tool_calls: dict[str, int] = {}
        self._current_tool_errors: dict[str, str] = {}
        self._current_turn_text = ""
        self._turn_tool_rounds = 0
        self._turn_assigned_agent = ""
        self._active_agent = ""
        self._pending_booking: dict | None = None
        self._booked_in_conversation = False
        self.agent_assignments: list[dict[str, str]] = []

    def _get_graph_config(self) -> dict[str, Any]:
        return {"configurable": {"thread_id": self.thread_id}}

    def bind_to_conversation_thread(self, thread_id: str) -> None:
        """Bind a compiled workflow instance to a dedicated conversation checkpoint."""
        if thread_id == self.thread_id:
            return
        self.thread_id = thread_id
        self._saver = _get_or_create_thread_checkpointer(thread_id)
        self._graph = self._build_graph()
        self.hangup_requested = False
        self.tool_calls_by_name = {}
        self.tool_errors = {}
        self.max_tool_rounds = 0
        self._current_tool_calls = {}
        self._current_tool_errors = {}
        self._current_turn_text = ""
        self._turn_tool_rounds = 0
        self._turn_assigned_agent = ""
        self.agent_assignments = []
        self._pending_booking = None
        self._current_turn_hangup = False
        self._turn_id = 0
        self.workflow_state = {}
        self.history_messages = []

    @staticmethod
    def release_thread(thread_id: str) -> None:
        """Free the checkpoint store for a finished conversation thread."""
        release_thread(thread_id)

    @staticmethod
    def _assigned_role(name: str) -> AgentRole | None:
        return next((role for role in AGENT_ROLES.values() if role.name == name), None)

    def _build_graph(self):
        graph = StateGraph(WorkflowState)
        graph.add_node("respond", self._respond)
        graph.add_node("tools", self._tools)
        graph.add_node("record_turn", self._record_turn)
        graph.add_node("tool_limit", self._tool_limit)
        graph.add_edge(START, "respond")
        graph.add_conditional_edges(
            "respond",
            self._route_after_response,
            {"tools": "tools", "done": "record_turn"},
        )
        graph.add_edge("tools", "respond")
        graph.add_edge("tool_limit", "record_turn")
        graph.add_edge("record_turn", END)
        return graph.compile(checkpointer=self._saver)

    async def _tool_limit(self, state: WorkflowState) -> dict[str, Any]:
        return {
            "messages": [
                AIMessage(
                    content="I’m having trouble completing that clinic lookup. Please call the front desk for help."
                )
            ]
        }

    async def _record_turn(self, state: WorkflowState) -> dict[str, Any]:
        messages = state.get("messages", [])
        if state.get("workflow", {}).get("human_handoff") == "hangup":
            return {"response_text": "HANGUP_CALL"}
        answer = next(
            (str(message.content) for message in reversed(messages) if isinstance(message, AIMessage) and not message.tool_calls),
            "",
        )
        return {"response_text": answer}

    async def _respond(self, state: WorkflowState) -> dict[str, Any]:
        errors = dict(state.get("tool_errors", {}))
        calls = {
            name: count
            for name, count in state.get("tool_calls_by_name", {}).items()
            if count
        }
        if state.get("turn_id") == self._turn_id:
            calls = {
                name: count
                for name, count in self._current_tool_calls.items()
                if count
            }
        if state.get("turn_id") == self._turn_id:
            errors.update(self._current_tool_errors)
        if state.get("workflow", {}).get("human_handoff") == "hangup":
            return {"messages": [AIMessage(content="HANGUP_CALL")]}
        history = list(state.get("messages", []))
        from langchain_core.messages import trim_messages

        conversation = trim_messages(
            history,
            max_tokens=4000,
            strategy="last",
            token_counter="approximate",
            include_system=True,
            start_on="human",
        )
        agent_name = (
            self._turn_assigned_agent
            if state.get("turn_id") == self._turn_id and self._turn_assigned_agent
            else state.get("assigned_agent")
        )
        # Keep the agent assignment stable while a turn is running through the
        # tool loop; only choose a fresh specialist for the next user turn.
        agent = self._assigned_role(agent_name) if agent_name else None
        agent = agent or route_to_agent(conversation, current=self._active_agent)
        if agent is None:
            return {"messages": [AIMessage(content="How can I help with your dental clinic visit?")]}
        assigned = {
            "assigned_agent": agent.name,
            "workflow": {
                **state.get("workflow", {}),
                "assigned_agent": agent.name,
                "agent_description": agent.description,
            },
        }
        assignment = {"agent": agent.name, "task": agent.description}
        if not self.agent_assignments or self.agent_assignments[-1] != assignment:
            self.agent_assignments.append(assignment)
        if state.get("turn_id") == self._turn_id:
            self._turn_assigned_agent = agent.name
            self._active_agent = agent.name
        assigned_toolset = self.tool_executor.tools
        scoped_tools = [
            candidate for candidate in assigned_toolset
            if getattr(candidate, "name", "") in agent.tools
            and not errors.get(getattr(candidate, "name", ""))
            and not calls.get(getattr(candidate, "name", ""))
        ]
        agent_llm = self.llm.bind_tools(scoped_tools)
        messages = messages_for_agent(
            agent,
            conversation,
            tool_errors=errors,
            tools_used=calls,
            max_tool_rounds=state.get("max_tool_rounds", 0),
        )
        for name, error in errors.items():
            messages.append(
                SystemMessage(
                    content=f"{name} has failed or was blocked ({error}). Do not retry it this turn. Ask for the needed information instead."
                )
            )
        human_handoff = state.get("workflow", {}).get("human_handoff")
        if human_handoff == "hangup" or self.hangup_requested or self._current_turn_hangup:
            return {"messages": [AIMessage(content="HANGUP_CALL")], **assigned}
        if not scoped_tools:
            response = await self.llm.ainvoke(messages)
            if self.cost_tracker is not None and hasattr(response, "usage_metadata") and response.usage_metadata:
                self.cost_tracker.add_llm_usage(response.usage_metadata, self.model)
            return {"messages": [response], **assigned}
        if self._turn_tool_rounds >= 3:
            messages.append(SystemMessage(content="Tool calls are exhausted for this turn. Reply now without calling any tools."))
            response = await self.llm.ainvoke(
                [SystemMessage(content=agent.system_prompt), *messages[1:]]
            )
            if self.cost_tracker is not None and hasattr(response, "usage_metadata") and response.usage_metadata:
                self.cost_tracker.add_llm_usage(response.usage_metadata, self.model)
            return {"messages": [response], **assigned}
        try:
            response = await agent_llm.ainvoke(messages)
        except Exception as error:
            if getattr(error, "status_code", None) == 429 or "rate_limit" in str(error).lower() or "rate limit" in str(error).lower():
                return {"messages": [AIMessage(content="RATE_LIMIT_BUSY")], **assigned}
            if not is_tool_validation_error(error):
                raise
            response = await self.llm.ainvoke(
                [SystemMessage(content=agent.system_prompt), *messages[1:]]
            )
        if self.cost_tracker is not None and hasattr(response, "usage_metadata") and response.usage_metadata:
            self.cost_tracker.add_llm_usage(response.usage_metadata, self.model)
        return {"messages": [response], **assigned}

    async def _tools(self, state: WorkflowState) -> dict[str, Any]:
        messages = list(state.get("messages", []))
        response = next(
            (message for message in reversed(messages) if isinstance(message, AIMessage) and message.tool_calls),
            None,
        )
        if response is None:
            return {}
        calls_by_name = dict(state.get("tool_calls_by_name", {}))
        if state.get("turn_id") == self._turn_id and not calls_by_name:
            calls_by_name = dict(self._current_tool_calls)
        errors = dict(state.get("tool_errors", {}))
        if state.get("turn_id") == self._turn_id and not errors:
            errors = dict(self._current_tool_errors)
        blocked: list[ToolMessage] = []
        assigned_agent = (
            self._turn_assigned_agent
            if self._turn_id == state.get("turn_id") and self._turn_assigned_agent
            else state.get("assigned_agent")
        )
        if any(call.get("name") == "escalate_to_human" for call in response.tool_calls):
            call = next(call for call in response.tool_calls if call.get("name") == "escalate_to_human")
            self._current_turn_hangup = True
            self.hangup_requested = True
            return {
                "handoff_requested": True,
                "workflow": {
                    **state.get("workflow", {}),
                    "human_handoff": "hangup",
                    "handoff_reason": (call.get("args") or {}).get("reason", "Caller requested human assistance."),
                },
                "messages": [ToolMessage(content="HANGUP_CALL", tool_call_id=call["id"])],
            }
        allowed_tools = {
            "dental_care": {"list_services", "list_providers", "escalate_to_human"},
            "appointments": {
                "list_services", "list_providers", "find_available_slots",
                "book_appointment", "get_appointments", "escalate_to_human",
            },
            "patient_records": {"find_patient_by_phone", "escalate_to_human"},
        }.get(assigned_agent, set())
        unauthorized = [call for call in response.tool_calls if call.get("name") not in allowed_tools]
        if unauthorized:
            updated_errors = {
                **errors,
                **{call.get("name", "tool"): "outside_agent_scope" for call in unauthorized},
            }
            self._current_tool_errors = updated_errors
            self._current_turn_text = state.get("turn_text", self._current_turn_text)
            self._turn_tool_rounds += 1
            self._current_tool_calls = calls_by_name
            self.tool_errors = updated_errors
            self.tool_calls_by_name = calls_by_name
            return {
                "messages": [
                    ToolMessage(
                        content=(
                            f"Tool {call.get('name')} is outside your assigned specialist role. "
                            "Continue without that tool and ask the patient for the next detail."
                        ),
                        tool_call_id=call["id"],
                    )
                    for call in unauthorized
                ] + blocked,
                "tool_errors": updated_errors,
                "tool_calls_by_name": calls_by_name,
                "max_tool_rounds": state.get("max_tool_rounds", 0) + 1,
            }
        if self._turn_tool_rounds >= 2:
            return {
                "messages": [
                    ToolMessage(
                        content="Tool limit reached. Ask the patient for missing details or explain the lookup without another tool call.",
                        tool_call_id=call["id"],
                    )
                    for call in response.tool_calls
                ] + blocked,
                "tool_errors": {
                    **errors,
                    **{call.get("name", "tool"): "tool_limit_reached" for call in response.tool_calls},
                },
                "tool_calls_by_name": calls_by_name,
                "max_tool_rounds": state.get("max_tool_rounds", 0) + 1,
            }
        if any(call.get("name") == "book_appointment" for call in response.tool_calls):
            call = next(call for call in response.tool_calls if call.get("name") == "book_appointment")
            args = call.get("args") or {}
            missing = [
                key
                for key in ("patient_name", "appointment_date", "appointment_time", "mobile_number")
                if not str(args.get(key) or "").strip()
            ]
            import re as _re

            # Don't trust confirmed_by_patient. Require a real read-back + affirmative.
            date_val = str(args.get("appointment_date") or "").strip()
            time_val = str(args.get("appointment_time") or "").strip()
            messages_list = list(state.get("messages", []))
            # find the AIMessage immediately preceding the tool-call response
            prev_ai = None
            for idx in range(len(messages_list) - 1, -1, -1):
                if messages_list[idx] is response:
                    # find previous AIMessage
                    for j in range(idx - 1, -1, -1):
                        if isinstance(messages_list[j], AIMessage):
                            prev_ai = messages_list[j]
                            break
                    break
            readback_ok = False
            if prev_ai is not None:
                content = str(prev_ai.content or "")
                readback_ok = (date_val and date_val in content) and (time_val and time_val in content)
            latest_user = str(state.get("turn_text") or "")
            affirmative = bool(_re.search(r"\b(yes|yeah|yep|correct|right|sure|go ahead|confirm)\b", latest_user.lower()))
            if missing or not str(args.get("service") or "").strip() or not readback_ok or not affirmative:
                detail = "Missing required details: " + ", ".join(missing) + ". " if missing else ""
                if not str(args.get("service") or "").strip():
                    detail += "Ask what problem or symptom the patient needs help with and pass that description as service. "
                if not readback_ok:
                    detail += "Read back name, date, time and reason in one sentence, then wait for a clear yes. "
                if not affirmative:
                    detail += "Wait for the patient to clearly confirm before booking. "
                errors["book_appointment"] = "booking_requires_confirmation_or_details"
                self._current_tool_errors = errors
                self._current_tool_calls = calls_by_name
                self._turn_tool_rounds += 1
                self.tool_errors = errors
                self.tool_calls_by_name = calls_by_name
                return {
                    "messages": [
                        ToolMessage(
                            content=(
                                "Booking blocked. " + detail
                                + "Repeat all booking details and ask the patient to explicitly confirm before submitting."
                            ),
                            tool_call_id=call["id"],
                        )
                    ] + blocked,
                    "tool_calls_by_name": calls_by_name,
                    "tool_errors": errors,
                    "max_tool_rounds": state.get("max_tool_rounds", 0) + 1,
                }
        calls_to_run = []
        for call in response.tool_calls:
            name = call.get("name", "")
            args = call.get("args") or {}
            if name == "find_patient_by_phone":
                import re

                digits = re.sub(r"\D", "", str(args.get("mobile_number") or ""))
                if not 10 <= len(digits) <= 15:
                    blocked.append(
                        ToolMessage(
                            content=(
                                "Invalid phone number: ask the patient for their real 10 to 15 digit phone number. "
                                "Do not retry this lookup with a placeholder."
                            ),
                            tool_call_id=call["id"],
                        )
                    )
                    errors[name] = "invalid_phone"
                    calls_by_name[name] = max(calls_by_name.get(name, 0), 1)
                    continue
                if errors.get(name):
                    blocked.append(
                        ToolMessage(
                            content="The phone lookup already failed. Ask the patient to verify their number; do not retry.",
                            tool_call_id=call["id"],
                        )
                    )
                    calls_by_name[name] = max(calls_by_name.get(name, 0), 1)
                    continue
            if name in ("list_services", "list_providers", "find_available_slots", "book_appointment") and calls_by_name.get(name, 0) >= 1:
                messages.append(
                    ToolMessage(
                        content=f"Do not call {name} again this turn. Use the previous result and ask the patient for the next required detail.",
                        tool_call_id=call["id"],
                    )
                )
                errors[name] = "duplicate_call_blocked"
                continue
            if calls_by_name.get(name, 0) >= 1:
                messages.append(
                    ToolMessage(
                        content=f"Do not call {name} again this turn. Ask the caller for missing details or explain the lookup failed.",
                        tool_call_id=call["id"],
                    )
                )
                errors[name] = "duplicate_call_blocked"
                continue
            calls_to_run.append(call)
        tool_start = len(messages)
        original_tool_calls = response.tool_calls
        if calls_to_run:
            response = response.model_copy(update={"tool_calls": calls_to_run})
            await self.tool_executor.execute_tool_calls(response, messages)
            for call in calls_to_run:
                name = call.get("name", "tool")
                calls_by_name[name] = calls_by_name.get(name, 0) + 1
            new_tool_messages = [
                message
                for message in messages
                if isinstance(message, ToolMessage)
                and message.tool_call_id in {call["id"] for call in calls_to_run}
            ]
            for tool_message in new_tool_messages:
                content = str(tool_message.content)
                if len(content) > 2000:
                    tool_message.content = content[:2000] + "...[truncated]" 
            for tool_message in new_tool_messages:
                content = str(tool_message.content).lower()
                if any(marker in content for marker in ("not found", "failed", "could not", "unreachable", "not signed in")):
                    name = next(call["name"] for call in calls_to_run if call["id"] == tool_message.tool_call_id)
                    errors[name] = "patient_not_found" if name == "find_patient_by_phone" and "not found" in content else "previous lookup failed"
                if "appointment booked" in content:
                    self._booked_this_turn = True
                    self._booking_confirmation = str(tool_message.content)
                    self._active_agent = ""
            if state.get("turn_id") == self._turn_id:
                self._current_tool_calls = dict(calls_by_name)
                self._current_tool_errors = dict(errors)
                self._turn_tool_rounds += 1
                self.tool_calls_by_name = dict(calls_by_name)
                self.tool_errors = dict(errors)
            return {
                "messages": new_tool_messages + blocked,
                "tool_calls_by_name": calls_by_name,
                "tool_errors": errors,
                "max_tool_rounds": state.get("max_tool_rounds", 0) + 1,
            }
        for call in original_tool_calls:
            name = call.get("name", "tool")
            if name in {item.get("name") for item in calls_to_run}:
                calls_by_name[name] = calls_by_name.get(name, 0) + 1
        new_tool_messages = [
            message
            for message in messages[tool_start:]
            if isinstance(message, ToolMessage)
        ]
        for tool_message in new_tool_messages:
            content = str(tool_message.content).lower()
            if any(term in content for term in ("failed", "not found", "unreachable", "not signed in", "could not")):
                name = next(
                    (call.get("name", "tool") for call in original_tool_calls if call.get("id") == tool_message.tool_call_id),
                    "tool",
                )
                errors[name] = "patient_not_found" if name == "find_patient_by_phone" and "not found" in content else "previous lookup failed"
                if "appointment booked" in content:
                    self._booked_this_turn = True
                    self._booking_confirmation = str(tool_message.content)
                    self._active_agent = ""
        self._pending_booking: dict | None = None
        self._booked_in_conversation = False
        if state.get("turn_id") == self._turn_id:
            self._turn_tool_rounds += 1
            self._current_tool_calls = dict(calls_by_name)
            self._current_tool_errors = dict(errors)
            self.tool_errors = dict(errors)
            self.tool_calls_by_name = dict(calls_by_name)
        result_messages = [
            message
            for message in new_tool_messages
            if isinstance(message, ToolMessage)
            and message.tool_call_id in {call["id"] for call in original_tool_calls}
        ]
        return {
            "messages": result_messages,
            "tool_calls_by_name": calls_by_name,
            "tool_errors": errors,
            "max_tool_rounds": state.get("max_tool_rounds", 0) + 1,
        }

    @staticmethod
    def _route_after_response(state: WorkflowState) -> str:
        response = next(
            (message for message in reversed(state.get("messages", [])) if isinstance(message, AIMessage)),
            None,
        )
        if not response:
            return "done"
        if response.tool_calls:
            return "tools"
        return "done"

    @staticmethod
    def _extract_text_from_message_content(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                block if isinstance(block, str) else str(block.get("text", ""))
                for block in content
                if isinstance(block, (str, dict))
            )
        return str(content or "")

    async def stream_conversation_turn(self, text: str, history: list | None = None):
        """Run one turn and yield generated text while preserving thread state."""
        if not self.guardrail.is_allowed(text):
            yield "I'm here to help with dental clinic questions and appointments. How can I help?"
            return
        from modules.common.emergency import is_emergency, emergency_instruction

        if is_emergency(text):
            yield emergency_instruction(text)
            self.hangup_requested = True
            yield "HANGUP_CALL"
            return
        config = self._get_graph_config()
        state = await self._graph.aget_state(config)
        if self.hangup_requested:
            yield "HANGUP_CALL"
            return
        if not state.values.get("messages") and history:
            seed = []
            for role, content in history:
                seed.append(HumanMessage(content=content) if role in ("human", "user") else AIMessage(content=content))
        else:
            seed = []
        self._current_turn_text = text
        self._turn_id += 1
        self.tool_calls_by_name = {}
        self.tool_errors = {}
        self._turn_assigned_agent = ""
        self._current_tool_calls = {}
        self._current_tool_errors = {}
        self._turn_tool_rounds = 0
        self._current_turn_hangup = False
        self._booked_this_turn = False
        # Conversation-level booking tracking stays (item 4)
        self._booking_confirmation = ""

        input_state: WorkflowState = {
            "messages": [*seed, HumanMessage(content=text)],
            "turn_text": text,
            "turn_id": self._turn_id,
            "workflow": state.values.get("workflow", {}),
            "tool_calls_by_name": dict(self._current_tool_calls),
            "tool_errors": dict(self._current_tool_errors),
            "max_tool_rounds": 0,
            "assigned_agent": self._turn_assigned_agent,
            "handoff_requested": self._current_turn_hangup,
        }
        try:
            if self._current_turn_hangup:
                yield "HANGUP_CALL"
                return
            if self.hangup_requested:
                yield "HANGUP_CALL"
                return
            async for update in self._graph.astream(input_state, config, stream_mode="updates"):
                for node_update in update.values():
                    if not isinstance(node_update, dict):
                        continue
                    if node_update.get("handoff_requested"):
                        self.hangup_requested = True
                        self.workflow_state = {
                            **node_update.get("workflow", {}),
                            "human_handoff": "hangup",
                        }
                        yield "HANGUP_CALL"
                        return
                    for message in node_update.get("messages", []):
                        if isinstance(message, AIMessage):
                            piece = self._extract_text_from_message_content(message.content)
                            # If the model claims a booking without a successful
                            # book_appointment result this turn, correct it.
                            if piece and not message.tool_calls:
                                lowered = piece.lower()
                                deferral_terms = ("call you back", "scheduling team", "staff will", "call you later", "forward your request")
                                is_deferral = any(term in lowered for term in deferral_terms)
                                import re as _re
                                claims_booking = bool(_re.search(r"\b(is|has been|have been|you're|you are) (booked|scheduled|all set|confirmed)\b|\bi('ve| have) booked\b", lowered))
                                if (not self._booked_this_turn and (claims_booking or is_deferral)):
                                    piece = (
                                        "I haven't booked that yet. Let me confirm the details with you first."
                                    )
                                elif (self._booked_this_turn and is_deferral and self._booking_confirmation):
                                    piece = self._booking_confirmation
                            # Yield any spoken content even when the message also
                            # carries tool calls, so a brief filler such as
                            # "Let me check that for you." is spoken while the
                            # tool runs instead of leaving the caller in silence.
                            if piece:
                                yield piece
                            elif message.tool_calls and not self._current_turn_hangup:
                                # The model did not provide a filler of its own;
                                # speak a short placeholder so the caller knows we
                                # are working on the request instead of silence.
                                tool_names = {
                                    str(call.get("name", ""))
                                    for call in (message.tool_calls or [])
                                }
                                if "book_appointment" in tool_names:
                                    yield "Booking that for you now."
                                elif "list_providers" in tool_names:
                                    yield "One moment, checking our dentists."
                                elif "list_services" in tool_names:
                                    yield "One moment, checking our services."
                                elif "find_available_slots" in tool_names:
                                    yield "Let me check open times."
                                elif "find_patient_by_phone" in tool_names:
                                    yield "Let me look up your record."
                                else:
                                    yield "Let me check that for you."
        finally:
            latest = await self._graph.aget_state(config)
            if latest.values.get("turn_id") == self._turn_id:
                self.tool_calls_by_name = dict(latest.values.get("tool_calls_by_name", {}))
                self.tool_errors = dict(latest.values.get("tool_errors", {}))
                self._current_tool_calls = dict(self.tool_calls_by_name)
                self._current_tool_errors = dict(self.tool_errors)
                self.max_tool_rounds = 0
                self._turn_assigned_agent = latest.values.get(
                    "assigned_agent", self._turn_assigned_agent
                )
                self._active_agent = latest.values.get("assigned_agent", self._active_agent)
            else:
                self.tool_calls_by_name = {}
                self.tool_errors = {}
                self.max_tool_rounds = 0
            self.workflow_state = {
                **latest.values.get("workflow", {}),
                **self.workflow_state,
            }
            self.history_messages = latest.values.get("messages", [])


_CHECKPOINTS: dict[str, InMemorySaver] = {}


def _get_or_create_thread_checkpointer(thread_id: str) -> InMemorySaver:
    """Return the saver dedicated to a conversation's thread id."""
    return _CHECKPOINTS.setdefault(thread_id, InMemorySaver())

def release_thread(thread_id: str) -> None:
    """Free the checkpoint store for a finished conversation thread.

    Prevents a later call from inheriting an earlier caller's conversation
    when thread ids are reused, and releases the held InMemorySaver.
    """
    _CHECKPOINTS.pop(thread_id, None)

