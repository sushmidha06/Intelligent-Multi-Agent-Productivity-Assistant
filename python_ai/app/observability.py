"""Structured logging, request correlation, and metrics for the AI service.

Three pieces:

1. `JsonFormatter` — emits one JSON log line per record. Easy to grep, easy
   to ship to Loki/Datadog/CloudWatch later. Adds the current request id
   automatically when one is in scope.

2. `request_id_middleware` — FastAPI middleware that mints a UUID per request,
   sets it on a contextvar, and echoes it back in the `X-Request-Id` header.
   The JSON formatter picks it up so every log line for the request can be
   joined back together.

3. `Metrics` — a tiny in-memory counter store. Exposed at `/metrics` in a
   Prometheus-compatible text format. Good enough for a demo / take-home;
   for real traffic you'd swap this for `prometheus_client`.
"""

from __future__ import annotations

import contextvars
import json
import logging
import time
import uuid
from collections import defaultdict

from fastapi import Request


# ContextVar so log records can stamp the current request id without each
# call site having to pass it through. asgi/anyio thread-locals would also
# work but contextvar is the modern asyncio-native option.
request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-"
)
user_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "user_id", default="-"
)


class JsonFormatter(logging.Formatter):
    """Render LogRecord as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id_ctx.get(),
            "user_id": user_id_ctx.get(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        # Surface any extras that callers passed via `logger.info("x", extra={...})`
        for k, v in record.__dict__.items():
            if k in {"args", "msg", "levelname", "levelno", "pathname", "filename",
                     "module", "exc_info", "exc_text", "stack_info", "lineno",
                     "funcName", "created", "msecs", "relativeCreated", "thread",
                     "threadName", "processName", "process", "name", "taskName"}:
                continue
            try:
                json.dumps(v)
                payload[k] = v
            except TypeError:
                payload[k] = str(v)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    """Replace the root handler with one that emits JSON. Call once at boot."""
    root = logging.getLogger()
    root.setLevel(level)
    # Remove default handlers so we don't get plain + JSON duplicate lines.
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    # Quiet down libraries that spam INFO.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


# ----- Metrics -------------------------------------------------------------

class Metrics:
    """In-memory counter + histogram store. Single-process only — fine for
    a single Render instance; for multi-instance you'd need a sidecar."""

    def __init__(self) -> None:
        self._counters: dict[str, float] = defaultdict(float)
        self._latencies: dict[str, list[float]] = defaultdict(list)

    def incr(self, name: str, value: float = 1.0, **labels: str) -> None:
        key = self._key(name, labels)
        self._counters[key] += value

    def observe(self, name: str, seconds: float, **labels: str) -> None:
        key = self._key(name, labels)
        # Cap memory — keep last 1000 observations per series.
        bucket = self._latencies[key]
        bucket.append(seconds)
        if len(bucket) > 1000:
            del bucket[: len(bucket) - 1000]

    @staticmethod
    def _key(name: str, labels: dict[str, str]) -> str:
        if not labels:
            return name
        parts = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
        return f"{name}{{{parts}}}"

    def render_prometheus(self) -> str:
        lines: list[str] = []
        for key, value in sorted(self._counters.items()):
            lines.append(f"{key} {value}")
        for key, values in sorted(self._latencies.items()):
            if not values:
                continue
            count = len(values)
            total = sum(values)
            avg = total / count
            mx = max(values)
            base = key.split("{", 1)[0]
            suffix = key[len(base):] if "{" in key else ""
            lines.append(f"{base}_count{suffix} {count}")
            lines.append(f"{base}_sum{suffix} {total}")
            lines.append(f"{base}_avg{suffix} {avg}")
            lines.append(f"{base}_max{suffix} {mx}")
        return "\n".join(lines) + "\n"


metrics = Metrics()


# ----- Middleware ----------------------------------------------------------

async def request_id_middleware(request: Request, call_next):
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    token = request_id_ctx.set(rid)
    start = time.monotonic()
    try:
        response = await call_next(request)
        elapsed = time.monotonic() - start
        metrics.incr("http_requests_total", path=request.url.path, status=str(response.status_code))
        metrics.observe("http_request_duration_seconds", elapsed, path=request.url.path)
        response.headers["x-request-id"] = rid
        return response
    except Exception:
        elapsed = time.monotonic() - start
        metrics.incr("http_requests_total", path=request.url.path, status="500")
        metrics.observe("http_request_duration_seconds", elapsed, path=request.url.path)
        raise
    finally:
        request_id_ctx.reset(token)
