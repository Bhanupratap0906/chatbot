"""Run:  python -m scripts.list_models

Prints every Groq model your API key can actually call, so you can pick a
valid value for GROQ_MODEL in .env.
"""

import os
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("GROQ_API_KEY")

if not api_key:
    raise SystemExit("Set GROQ_API_KEY in .env")


def main():
    try:
        from groq import Groq
    except ImportError:
        raise SystemExit("Groq SDK not installed. Run: pip install groq")

    client = Groq(api_key=api_key)

    try:
        # Fetch the list of available models from the Groq API
        models_page = client.models.list()
        
        for model in models_page.data:
            # Filters out text-to-speech or other non-LLM models if necessary.
            # Groq model objects contain fields like id, object, created, and owned_by.
            print(model.id)

    except Exception as e:
        print(f"Error fetching models: {e}")


if __name__ == "__main__":
    main()
