"""Shared tool registry (OCP).

Lives in its own module so every tool file (``appointment``,
``portal_tools``, future ones) can append to the same list without
import-order cycles. ``TOOL_REGISTRY`` stays re-exported from
``modules.tools.appointment_booking_tool`` for existing importers.
"""

from __future__ import annotations

# LlmToolCallExecutor iterates this list; new tools are added by registering here.
TOOL_REGISTRY: list = []
