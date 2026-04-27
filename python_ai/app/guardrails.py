"""Safety + validation layer that wraps every chat request.

Three classes of guardrail:

1. **Input validation** — reject empty/oversized messages and obvious
   prompt-injection attempts before they reach the LLM.

2. **Rate limiting** — per-user sliding-window counter so a single tenant
   can't blow through the Gemini quota for everyone else.

3. **Output filtering** — best-effort PII redaction on the final assistant
   response (emails, phone numbers, credit-card-shaped digit runs). It's a
   regex pass, not a guarantee — the README documents this honestly.

`GuardrailViolation` is raised on hard rejections. The caller (the `/chat`
handler) maps it to an HTTP 400/429.
"""

from __future__ import annotations

import re
import time
from collections import deque
from threading import Lock


MAX_MESSAGE_CHARS = 4000
MAX_HISTORY_MESSAGES = 20
RATE_LIMIT_PER_HOUR = 30
RATE_WINDOW_SECONDS = 3600


# Simple keyword/phrase list for prompt-injection screening. Conservative on
# purpose — false positives are worse than false negatives for a freelance
# copilot since legitimate users don't usually say "ignore previous
# instructions" in normal queries.
INJECTION_PATTERNS = [
    re.compile(r"\bignore\s+(?:all|the|your|previous|above)\s+(?:instructions|prompt|rules)\b", re.I),
    re.compile(r"\bdisregard\s+(?:all|the|your|previous|above)\b", re.I),
    re.compile(r"\bsystem\s+prompt\b", re.I),
    re.compile(r"\byou\s+are\s+now\s+(?:a|an)\s+\w+", re.I),
    re.compile(r"\breveal\s+(?:your|the)\s+(?:prompt|instructions|system)\b", re.I),
    re.compile(r"</?\s*(?:system|admin|developer)\s*>", re.I),
]


# PII patterns for output redaction. Email + phone are reliable; the credit-card
# pattern catches the obvious shape (13-19 digits with optional separators).
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
PHONE_RE = re.compile(r"\b(?:\+?\d{1,3}[\s-]?)?\(?\d{3}\)?[\s-]?\d{3}[\s-]?\d{4}\b")
CARD_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")


class GuardrailViolation(Exception):
    """Raised when a request fails a guardrail check.
    `code` is mapped to an HTTP status by the caller."""

    def __init__(self, message: str, code: str = "invalid"):
        super().__init__(message)
        self.code = code


# ----- Input validation -----------------------------------------------------

def validate_message(message: str) -> str:
    if not isinstance(message, str):
        raise GuardrailViolation("message must be a string", code="invalid")
    msg = message.strip()
    if not msg:
        raise GuardrailViolation("message is empty", code="invalid")
    if len(msg) > MAX_MESSAGE_CHARS:
        raise GuardrailViolation(
            f"message too long (max {MAX_MESSAGE_CHARS} chars)", code="invalid"
        )
    return msg


def validate_history(history: list | None) -> list:
    if history is None:
        return []
    if not isinstance(history, list):
        raise GuardrailViolation("history must be a list", code="invalid")
    if len(history) > MAX_HISTORY_MESSAGES:
        # Trim to the most recent N rather than reject — better UX than 400.
        history = history[-MAX_HISTORY_MESSAGES:]
    return history


def detect_injection(message: str) -> str | None:
    """Return the matching pattern's source string if injection is detected,
    else None. The caller decides whether to block or just log + flag."""
    for pat in INJECTION_PATTERNS:
        if pat.search(message):
            return pat.pattern
    return None


# ----- Rate limiting --------------------------------------------------------

class _SlidingWindowLimiter:
    """In-memory sliding-window counter, keyed by user_id.

    Memory grows with active users. Acceptable for a single Render instance;
    swap for Redis if you scale out. We use a deque per user and prune
    entries older than the window on every check — O(N) per check where N is
    that user's recent activity, bounded by the limit itself."""

    def __init__(self, limit: int, window_seconds: float):
        self.limit = limit
        self.window = window_seconds
        self._buckets: dict[str, deque[float]] = {}
        self._lock = Lock()

    def check(self, user_id: str) -> tuple[bool, int]:
        """Returns (allowed, remaining). Records the hit if allowed."""
        now = time.monotonic()
        cutoff = now - self.window
        with self._lock:
            bucket = self._buckets.setdefault(user_id, deque())
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= self.limit:
                return False, 0
            bucket.append(now)
            return True, self.limit - len(bucket)


_limiter = _SlidingWindowLimiter(RATE_LIMIT_PER_HOUR, RATE_WINDOW_SECONDS)


def check_rate_limit(user_id: str) -> int:
    """Raises GuardrailViolation(code='rate_limited') when the user is over
    quota. Returns remaining hits in the window when allowed."""
    allowed, remaining = _limiter.check(user_id)
    if not allowed:
        raise GuardrailViolation(
            f"rate limit exceeded ({RATE_LIMIT_PER_HOUR}/hour)", code="rate_limited"
        )
    return remaining


# ----- Output filtering -----------------------------------------------------

def redact_pii(text: str) -> tuple[str, int]:
    """Redact emails, phone numbers, and card-shaped digit runs.
    Returns (redacted_text, redactions_made)."""
    if not text:
        return text, 0
    count = 0

    def _email(m):
        nonlocal count
        count += 1
        return "[redacted-email]"

    def _phone(m):
        nonlocal count
        count += 1
        return "[redacted-phone]"

    def _card(m):
        nonlocal count
        # Reject obvious false positives: short runs of digits separated by
        # spaces show up in invoice IDs etc. Only redact 13+ contiguous digits
        # after stripping separators.
        raw = re.sub(r"[\s-]", "", m.group(0))
        if len(raw) < 13:
            return m.group(0)
        count += 1
        return "[redacted-card]"

    out = EMAIL_RE.sub(_email, text)
    out = PHONE_RE.sub(_phone, out)
    out = CARD_RE.sub(_card, out)
    return out, count
