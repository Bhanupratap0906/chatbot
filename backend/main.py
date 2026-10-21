import uvicorn
from fastapi import FastAPI

from modules.realtime.voice_controller import router as voice_router


app = FastAPI(
    title="Voice AI",
)

# Register the live-call websocket route.
app.include_router(voice_router)


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
