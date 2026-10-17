"""Tool-call execution for the LLM turn (SRP + OCP).

``ToolExecutor`` owns the ``response.tool_calls`` loop: validation of
``book_appointment`` args, error mapping, ``ToolMessage`` building.
The LLM service only orchestrates prompt -> tool -> final stream.

OCP: tools come from an injected registry (defaults to
``TOOL_REGISTRY``); adding a tool never edits this executor — register
it and add a branch-free generic path. ``book_appointment`` keeps a
dedicated missing-args path because small models emit partial calls.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import ToolMessage


class ToolExecutor:
    def __init__(self, tools: list | None = None):
        # OCP: injectable registry; defaults to the clinic's registry.
        if tools is not None:
            self._tools = list(tools)
        else:
            from modules.tools.appointment import TOOL_REGISTRY
            self._tools = list(TOOL_REGISTRY)
        self._by_name = {getattr(t, "name", t.__class__.__name__): t for t in self._tools}

    @property
    def tools(self) -> list:
        return list(self._tools)

    async def execute(self, response, messages: list) -> None:
        """Run each tool call and append ``ToolMessage``s to ``messages`` (mutates)."""
        for tool_call in response.tool_calls or []:
            tool_name = tool_call["name"]
            tool_args = tool_call["args"] or {}
            print(f"[tool] name: {tool_name}")
            print(f"[tool] args: {tool_args}")

            if tool_name == "book_appointment":
                await self._run_booking(tool_call, tool_args, messages)
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
                print(f"[tool] {tool_name} failed: {error!r}")
                result = f"{tool_name} failed due to a technical issue. Apologize briefly and continue."
            print(f"[tool] result: {result}")
            messages.append(ToolMessage(content=str(result), tool_call_id=tool_call["id"]))

    async def _run_booking(self, tool_call: dict, tool_args: dict[str, Any], messages: list) -> None:
        missing = [
            field
            for field in ("patient_name", "appointment_date", "appointment_time")
            if not str(tool_args.get(field) or "").strip()
        ]
        if missing:
            print(f"[tool] missing args: {missing} — asking user")
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
        tool = self._by_name.get("book_appointment")
        try:
            if tool is None:
                from modules.tools.appointment import book_appointment as _tool
                tool = _tool
            tool_result = await tool.ainvoke(tool_args)
        except Exception as error:
            print(f"[tool] booking failed: {error!r}")
            tool_result = (
                "Booking failed due to a technical issue. "
                "Apologize briefly and ask the patient to "
                "confirm the details so you can try again."
            )
        print(f"[tool] result: {tool_result}")
        messages.append(ToolMessage(content=str(tool_result), tool_call_id=tool_call["id"]))
