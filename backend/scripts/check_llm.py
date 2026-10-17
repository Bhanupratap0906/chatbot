"""Run:  python -m scripts.check_llm"""

import asyncio
import os
import sys
import time

import httpx
from dotenv import load_dotenv, find_dotenv


def main():
    dotenv_path = find_dotenv(usecwd=True)
    print(f".env path : {dotenv_path or '(not found)'}")
    load_dotenv(dotenv_path)

    api_key = os.getenv("GROQ_API_KEY", "")
    model   = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")

    print(f"GROQ_API_KEY : {'set (' + api_key[:8] + '…)' if api_key else 'NOT SET'}")

    if not api_key:
        print("Fix: add GROQ_API_KEY=gsk_... to your .env and re-run.")
        sys.exit(1)

    # ---- list every model this key can actually call ----
    print("\nFetching models available for your key...")
    r = httpx.get(
        "https://api.groq.com/openai/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=10,
    )

    if r.status_code != 200:
        print(f"Models endpoint returned {r.status_code}: {r.text[:400]}")
        sys.exit(1)

    available = sorted(m["id"] for m in r.json()["data"])
    print(f"\nAvailable models ({len(available)}):")
    for m in available:
        print(f"  {m}")

    # ---- pick a model ----
    if model in available:
        chosen = model
        print(f"\nGROQ_MODEL={model!r} is available — using it.")
    else:
        print(f"\nGROQ_MODEL={model!r} is NOT in your available list.")
        # prefer instant/fast models for voice
        preferred = [
            "llama-3.1-8b-instant",
            "llama3-8b-8192",
            "llama-3.1-70b-versatile",
            "llama3-70b-8192",
            "gemma2-9b-it",
            "mixtral-8x7b-32768",
        ]
        chosen = next((m for m in preferred if m in available), available[0] if available else None)

        if not chosen:
            print("No models available at all — check your Groq account.")
            sys.exit(1)

        print(f"Falling back to: {chosen!r}")
        print(f"\nAdd this to your .env to make it permanent:\n  GROQ_MODEL={chosen}")

    # ---- quick stream test ----
    print(f"\nTesting stream with {chosen!r}...")

    from langchain_groq import ChatGroq

    llm = ChatGroq(model=chosen, temperature=0, max_retries=0, timeout=15)

    async def run():
        start = time.perf_counter()
        first = None
        text  = ""

        async for chunk in llm.astream([
            ("system", "Reply in one short sentence."),
            ("human",  "Hi, I want to book an appointment."),
        ]):
            piece = chunk.content if isinstance(chunk.content, str) else ""

            if piece:
                if first is None:
                    first = time.perf_counter() - start
                    print(f"first token : {first:.2f}s")
                text += piece

        total = time.perf_counter() - start
        print(f"total time  : {total:.2f}s")
        print(f"answer      : {text}")

    asyncio.run(run())


if __name__ == "__main__":
    main()