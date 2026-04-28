import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # The Node backend we call for per-user Firestore data and decrypted integration secrets.
    NODE_API_BASE_URL: str = os.getenv("NODE_API_BASE_URL", "http://localhost:3001/api")

    # Shared HS256 secret. Node signs service tokens, Python verifies.
    JWT_SHARED_SECRET: str = os.getenv("JWT_SHARED_SECRET", "")

    # Gemini
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    # Hardcoded — earlier deployments had `gemini-2.0-flash` baked into Render's
    # env vars, and that model is no longer available for newer Google AI Studio
    # API keys. Pinning here so the app behaves identically regardless of host
    # env. Set GEMINI_MODEL_OVERRIDE if you really need to change it at runtime.
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL_OVERRIDE", "gemini-2.5-flash")
    GEMINI_EMBED_MODEL: str = os.getenv("GEMINI_EMBED_MODEL", "models/gemini-embedding-001")

    # RAG — Chroma Cloud (per-tenant collection inside this database).
    # Falls back to in-memory numpy cosine if Chroma keys are missing.
    CHROMA_API_KEY: str  = os.getenv("CHROMA_API_KEY", "")
    CHROMA_TENANT: str   = os.getenv("CHROMA_TENANT", "")
    CHROMA_DATABASE: str = os.getenv("CHROMA_DATABASE", "freelance-mcp")

    # Agent loop
    AGENT_MAX_ITERATIONS: int = int(os.getenv("AGENT_MAX_ITERATIONS", "8"))

    # Shared secret for the cron / scheduler endpoints. Anything calling
    # /agents/run must present this in the X-Cron-Secret header. Distinct
    # from JWT_SHARED_SECRET so leaking one doesn't grant the other.
    CRON_SHARED_SECRET: str = os.getenv("CRON_SHARED_SECRET", "")

    # Rate limiting — Upstash Redis (serverless).
    # If these are missing, we fall back to an in-memory deque limiter.
    UPSTASH_REDIS_REST_URL: str   = os.getenv("UPSTASH_REDIS_REST_URL", "")
    UPSTASH_REDIS_REST_TOKEN: str = os.getenv("UPSTASH_REDIS_REST_TOKEN", "")


settings = Settings()
