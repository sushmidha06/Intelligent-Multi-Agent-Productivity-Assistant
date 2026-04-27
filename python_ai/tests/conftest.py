"""Test bootstrap. Sets env vars before any app modules import so tests
don't need real API keys."""

import os
import sys
from pathlib import Path

os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")
os.environ.setdefault("JWT_SHARED_SECRET", "test-jwt-secret")
os.environ.setdefault("NODE_API_BASE_URL", "http://localhost:9999/api")

# Make `app.*` importable when running pytest from repo root or python_ai/.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
