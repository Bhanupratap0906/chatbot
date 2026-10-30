"""Clinic tools package: importing it registers every tool (OCP).

``tool_executor`` reads ``TOOL_REGISTRY``; importing this package pulls in
the tool modules so each one can append its own entries.
"""

from modules.tools.tool_registry import TOOL_REGISTRY
from modules.tools import appointment_booking_tool, portal_lookup_tools  # noqa: F401

__all__ = ["TOOL_REGISTRY", "appointment_booking_tool", "portal_lookup_tools"]
