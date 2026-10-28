"""Shared pytest fixtures and environment setup for backend tests.

This file must stay import-safe: it is loaded before any test module, so it
sets the environment needed by ``modules.common.settings`` first.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Dummy credentials so settings.py can be imported without a real .env.
# Never put real API keys here.
os.environ.setdefault("GROQ_API_KEY", "test-groq-api-key")
os.environ.setdefault("DEEPGRAM_API_KEY", "test-deepgram-api-key")

# Make ``import main`` (and ``modules.*``) work regardless of the pytest rootdir.
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
