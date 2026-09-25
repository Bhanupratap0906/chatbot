from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

BASE_DIR = Path(__file__).resolve().parent

# Load backend/.env regardless of the working directory uvicorn is
# started from (modules also call load_dotenv() as a fallback).
load_dotenv(BASE_DIR / ".env")

from modules.stt.stt_controller import router as stt_router
from modules.tts.tts_controller import router as tts_router
from modules.realtime.voice_controller import router as voice_router


app = FastAPI(
    title="Voice AI",
)

# Allow the Next.js frontend (dev on :3000, prod on its own origin) to reach
# the API + /ws/voice. WebSockets don't use CORS, but the REST routers do.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Register routers
app.include_router(stt_router) 
app.include_router(tts_router)
app.include_router(voice_router)


@app.get("/")
async def root():
    """Serve the static landing page when the browser requests ``/``; registered on the FastAPI app below."""
    return FileResponse(BASE_DIR / "static" / "index.html")


def start():
    """Start the local FastAPI server; used by the ``python main.py`` entry point."""
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )


if __name__ == "__main__":
    start()
