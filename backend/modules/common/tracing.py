"""LangSmith tracing setup (SRP, optional).

Enabled only when LANGSMITH_TRACING=true and LANGSMITH_API_KEY is set.
Inputs/outputs are anonymized with redact() before upload.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("tracing")


def init_tracing() -> None:
    """Configure LangSmith tracing with an anonymizing client (if enabled)."""
    from modules.common.settings import get_settings

    settings = get_settings()
    if not settings.langsmith_tracing or not settings.langsmith_api_key:
        return
    try:
        from langsmith import Client
        from langsmith.anonymizer import create_anonymizer
        from langsmith.run_trees import configure

        from modules.common.redact import redact

        anonymizer = create_anonymizer(redact)
        client = Client(api_key=settings.langsmith_api_key, anonymizer=anonymizer)
        configure(client=client, enabled=True)
        os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
        os.environ.setdefault("LANGCHAIN_PROJECT", settings.langsmith_project)
        logger.info("LangSmith tracing enabled (anonymized)")
    except Exception as error:  # noqa: BLE001
        logger.warning("tracing init failed: %r", error)


__all__ = ["init_tracing"]
