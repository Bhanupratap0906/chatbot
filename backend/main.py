"""FastAPI entry point for the Voice AI backend.

Configures structured application-wide logging so every subsystem emits
consistent, greppable lines (timestamp, level, logger, message). API,
booking, and tool logs all flow through this format.
"""

import logging

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from modules.portal.appointments_router import router as appointments_router
from modules.portal.auth_router import router as auth_router
from modules.sip.sip_credentials_router import router as sip_router
from modules.common.settings import get_settings
from modules.realtime.voice_websocket_controller import router as voice_router


def configure_structured_application_logging() -> None:
    """Install one structured log format for every module logger.

    Format: ``<time> | <LEVEL> | <logger> | <message>``. Third-party loggers
    (httpx, uvicorn.access) are kept quieter so app/API/tool lines stay readable.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
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
    yield


app = FastAPI(
    title="Voice AI",
    lifespan=lifespan,
)


@app.get("/health")
async def health_check():
    """Basic process liveness endpoint for local/dev health checks."""
    return {"status": "ok"}

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
