"""Tool-call execution for the LLM turn (SRP + OCP).

``LlmToolCallExecutor`` owns the ``response.tool_calls`` loop: validation of
``book_appointment`` args, error mapping, ``ToolMessage`` building.
The LLM service only orchestrates prompt -> tool -> final stream.

OCP: tools come from an injected registry (defaults to
``TOOL_REGISTRY``); adding a tool never edits this executor — register
it and add a branch-free generic path. ``book_appointment`` keeps a
dedicated missing-args path because small models emit partial calls.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import ToolMessage

logger = logging.getLogger("tool")


class LlmToolCallExecutor:
    """Dispatch model-requested tools and append their results to the LLM messages."""

    def __init__(self, tools: list | None = None):
        """Load registered clinic tools or use an explicitly supplied list."""
        # OCP: injectable registry; defaults to the clinic's registry.
        if tools is not None:
            self._tools = list(tools)
        else:
            from modules.tools import TOOL_REGISTRY  # registers tools on import
            self._tools = list(TOOL_REGISTRY)
        self._by_name = {getattr(t, "name", t.__class__.__name__): t for t in self._tools}

    @property
    def tools(self) -> list:
        """Return registered tools; used to bind tools to the language model."""
        return list(self._tools)

    async def execute_tool_calls(self, response, messages: list) -> None:
        """Run each tool call and append ``ToolMessage``s to ``messages`` (mutates)."""
        for tool_call in response.tool_calls or []:
            tool_name = tool_call["name"]
            tool_args = tool_call["args"] or {}
            logger.info("executing tool=%s args=%s", tool_name, tool_args)

            if tool_name == "book_appointment":
                await self._validate_and_execute_booking(tool_call, tool_args, messages)
                continue

            # Generic OCP path: any registered tool works without new branches.
            tool = self._by_name.get(tool_name)
            if tool is None:
                messages.append(
                    ToolMessage(
                        content=f"Unknown tool: {tool_name}. Do not call it again.",
                        tool_call_id=tool_call["id"],
                    )
                )
                continue
            try:
                result = await tool.ainvoke(tool_args)
            except Exception as error:
                logger.warning("tool=%s failed: %r", tool_name, error)
                result = f"{tool_name} failed due to a technical issue. Apologize briefly and continue."
            logger.info("tool=%s result=%s", tool_name, result)
            messages.append(ToolMessage(content=str(result), tool_call_id=tool_call["id"]))

    async def _validate_and_execute_booking(self, tool_call: dict, tool_args: dict[str, Any], messages: list) -> None:
        """Validate required appointment fields and append the booking result to the model conversation."""
        missing = [
            field
            for field in ("patient_name", "appointment_date", "appointment_time", "mobile_number")
            if not str(tool_args.get(field) or "").strip()
        ]
        if missing:
            logger.info("tool=book_appointment missing args=%s — asking user", missing)
            messages.append(
                ToolMessage(
                    content=(
                        "Cannot book yet — still missing: "
                        + ", ".join(missing)
                        + ". Ask the patient for the missing information."
                    ),
                    tool_call_id=tool_call["id"],
                )
            )
            return
        if tool_args.get("confirmed_by_patient") is not True:
            messages.append(
                ToolMessage(
                    content=(
                        "Booking blocked because the patient has not explicitly confirmed. "
                        "Repeat the booking details and ask for confirmation before trying again."
                    ),
                    tool_call_id=tool_call["id"],
                )
            )
            return
        tool = self._by_name.get("book_appointment")
        try:
            if tool is None:
                from modules.tools.appointment_booking_tool import book_appointment as _tool
                tool = _tool
            tool_result = await tool.ainvoke(tool_args)
        except Exception as error:
            logger.warning("tool=book_appointment failed: %r", error)
            tool_result = (
                "Booking failed due to a technical issue. "
                "Apologize briefly and ask the patient to "
                "confirm the details so you can try again."
            )
        logger.info("tool=book_appointment result=%s", tool_result)
        messages.append(ToolMessage(content=str(tool_result), tool_call_id=tool_call["id"]))
