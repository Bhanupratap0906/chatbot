"""Usage-capturing async client wrapper (moved from groq_llm_assistant)."""

from __future__ import annotations

from collections.abc import Mapping


class _UsageCapturingAsyncClient:
    """Forward streaming responses while retaining usage-only chunks."""

    def __init__(self, client, on_usage):
        self._client = client
        self._on_usage = on_usage

    async def create(self, *args, **kwargs):
        stream = self._client.create(*args, **kwargs)
        if hasattr(stream, "__await__"):
            stream = await stream

        if not hasattr(stream, "__aiter__"):
            return stream

        async def with_usage():
            async for chunk in stream:
                if isinstance(chunk, Mapping):
                    data = chunk
                elif hasattr(chunk, "model_dump"):
                    data = chunk.model_dump()
                else:
                    data = {}
                usage = data.get("usage")
                if usage:
                    self._on_usage(usage)
                yield chunk

        return with_usage()
