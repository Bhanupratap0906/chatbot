"""FastAPI entry point for the Voice AI backend.

Configures structured application-wide logging so every subsystem emits
consistent, greppable lines (timestamp, level, logger, message). API,
booking, and tool logs all flow through this format.
"""

import logging
import os

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from modules.portal.appointments_router import router as appointments_router
from modules.portal.auth_router import router as auth_router
from modules.sip.sip_credentials_router import router as sip_router
from modules.common.call_context import CallIDFilter, JSONFormatter, get_call_id, set_call_id
from modules.common.settings import get_settings
from modules.realtime.voice_websocket_controller import router as voice_router
from modules.portal.portal_http_client import get_shared_portal_http_client


def configure_structured_application_logging() -> None:
    """Install one structured log format for every module logger.

    JSON logging is enabled when ``LOG_FORMAT=json``; otherwise a readable
    text format is used. A ``CallIDFilter`` injects the per-call ``call_id``
    into every record.
    """
    use_json = os.getenv("LOG_FORMAT", "text").lower() == "json"
    if use_json:
        formatter = JSONFormatter(datefmt="%Y-%m-%dT%H:%M:%S")
    else:
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)-7s | %(name)s | [%(call_id)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    handler.addFilter(CallIDFilter())

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers = [handler]
    # Keep noisy third-party HTTP/uvicorn access logs below app chatter.
    for noisy in ("httpx", "httpcore", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


configure_structured_application_logging()

@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if not settings.portal_location_encryption_key:
        raise RuntimeError(
            "Missing required environment variable: PORTAL_LOCATION_ENCRYPTION_KEY"
        )
    from modules.common.tracing import init_tracing

    init_tracing()
    yield
    # Close the shared portal httpx client on shutdown.
    try:
        from modules.portal.portal_http_client import get_shared_portal_http_client

        await get_shared_portal_http_client().aclose()
    except Exception:
        pass


app = FastAPI(
    title="Voice AI",
    lifespan=lifespan,
)


@app.get("/health")
async def health_check():
    """Basic process liveness endpoint for local/dev health checks."""
    return {"status": "ok"}

@app.get("/ready")
async def ready_check():
    """Readiness: portal reachable and Deepgram configured."""
    settings = get_settings()
    portal_status = "ok"
    try:
        client = get_shared_portal_http_client()
        # Best-effort: just ensure the client exists; avoid a network call
        # that could block. Report config presence instead.
        if not settings.portal_base_url:
            portal_status = "down"
    except Exception:
        portal_status = "down"
    deepgram_status = "ok" if settings.deepgram_api_key else "down"
    status_code = 200 if portal_status == "ok" and deepgram_status == "ok" else 503
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=status_code,
        content={"portal": portal_status, "deepgram": deepgram_status},
    )

# Cookie-based portal auth requires credentialed CORS with explicit origins
# (browsers reject `*` + credentials). Next.js dev server runs on :3000.
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register the live-call websocket route.
app.include_router(voice_router)

# Customer-portal session (login page -> portal JWT -> chatbot cookie)
# and the appointment list/details/book proxies that replay it as Bearer.
app.include_router(auth_router)
app.include_router(appointments_router)
app.include_router(sip_router)


def start_fastapi_server():
    """Start the local FastAPI server; used by the ``python main.py`` entry point."""
    uvicorn.run(
        "main:app",
        port=8000,
    )


if __name__ == "__main__":
    start_fastapi_server()
