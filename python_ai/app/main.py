"""FastAPI entrypoint for the Sushmi MCP AI service.

- `/health`       — liveness probe
- `/metrics`      — Prometheus-format counters/histograms
- `/chat`         — multi-agent chat (Planner -> Executor) with guardrails
- `/mcp/servers`  — debug: lists MCP servers + tools (auth'd)
"""

from __future__ import annotations

import logging
import traceback
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

import asyncio
import hashlib
import hmac
import json as _json
import time as _time

import httpx

from .agent import Orchestrator
from .agents import ALL_AGENT_CLASSES
from .node_client import NodeClient
from .guardrails import (
    GuardrailViolation,
    check_rate_limit,
    detect_injection,
    redact_pii,
    validate_history,
    validate_message,
)
from .observability import (
    configure_logging,
    metrics,
    request_id_ctx,
    request_id_middleware,
    user_id_ctx,
)
from .security import require_user
from .settings import settings

configure_logging()
log = logging.getLogger("sushmi.ai")

app = FastAPI(title="Sushmi MCP AI Service", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Only the Node backend calls this; it's already behind auth.
    allow_methods=["*"],
    allow_headers=["*"],
)
app.middleware("http")(request_id_middleware)


@app.exception_handler(Exception)
async def all_exceptions_handler(_: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error")
    return JSONResponse(
        status_code=500,
        content={
            "detail": f"{type(exc).__name__}: {exc}",
            "trace": traceback.format_exc().splitlines()[-8:],
            "request_id": request_id_ctx.get(),
        },
    )


class ChatMessage(BaseModel):
    role: str = Field(..., description="'user' or 'assistant'")
    content: str


class ChatRequest(BaseModel):
    message: str
    history: list[ChatMessage] = Field(default_factory=list)


class ToolCallTrace(BaseModel):
    tool: str | None
    input: Any | None
    output: str


class ChatResponse(BaseModel):
    response: str
    tool_calls: list[ToolCallTrace]
    tools_available: list[str]
    plan: str | None = None
    pii_redactions: int = 0


@app.get("/")
def root() -> dict:
    return {
        "service": "sushmi-mcp-ai",
        "status": "running",
        "endpoints": {
            "health": "/health",
            "metrics": "/metrics",
            "mcp_servers": "/mcp/servers",
            "chat": "/chat (POST)",
            "docs": "/docs",
        },
    }


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "sushmi-mcp-ai",
        "model": settings.GEMINI_MODEL,
        "embed_model": settings.GEMINI_EMBED_MODEL,
        "configured": bool(settings.GEMINI_API_KEY) and bool(settings.JWT_SHARED_SECRET),
        "build": "v10-multi-agent-2025-04-27",
    }


@app.get("/metrics", response_class=PlainTextResponse)
def get_metrics() -> str:
    """Prometheus-format metrics. No auth — same posture as `/health`."""
    return metrics.render_prometheus()


@app.get("/mcp/servers")
def list_mcp_servers(claims: dict = Depends(require_user)) -> dict:
    orch = Orchestrator(user_id=claims["userId"], email=claims.get("email"))
    try:
        catalog = []
        for server in orch.servers:
            catalog.append({
                "server_name": server.server_name,
                "server_version": server.server_version,
                "tools": server.list_tools(),
            })
        return {"userId": claims["userId"], "servers": catalog}
    finally:
        orch.close()


def _secret_fingerprint(secret: str) -> str:
    """First 8 hex chars of sha256(secret). Safe to log: 32 bits of entropy is
    enough to spot a copy-paste mismatch but useless for recovering the secret
    (~1 in 4 billion collisions). Lets us prove both sides are using the same
    SLACK_SIGNING_SECRET without ever revealing it."""
    if not secret:
        return "(empty)"
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:8]


def _verify_slack_signature(timestamp: str, signature: str, raw_body: bytes) -> tuple[bool, str]:
    """Recompute Slack's HMAC-SHA256 over `v0:{ts}:{body}` and constant-time
    compare. Returns (ok, diagnostic) — diagnostic is a short reason string
    used only when verification fails, for log triage."""
    secret = settings.SLACK_SIGNING_SECRET
    if not secret:
        return False, "no_secret_configured"
    if not timestamp or not signature:
        return False, "missing_headers"
    try:
        skew = abs(int(_time.time()) - int(timestamp))
        if skew > 60 * 5:
            return False, f"stale_timestamp_skew={skew}s"
    except ValueError:
        return False, "non_numeric_timestamp"
    base = f"v0:{timestamp}:".encode("utf-8") + raw_body
    expected = "v0=" + hmac.new(secret.encode("utf-8"), base, hashlib.sha256).hexdigest()
    if hmac.compare_digest(expected, signature):
        return True, "ok"
    # Log signature *prefixes* (first 10 chars) so we can eyeball whether the
    # mismatch is a wrong-secret problem (full divergence) vs a body-canon
    # problem (matching prefix). Never log the full signature.
    return False, f"hmac_mismatch expected={expected[:10]}.. got={signature[:10]}.."


async def _process_slack_event(user_id: str, channel: str, text: str, bot_token: str) -> None:
    """Runs the orchestrator for a Slack message, then posts the reply back to
    the channel. Lives in a background task so the webhook can ack within
    Slack's 3-second deadline."""
    try:
        orch = Orchestrator(user_id=user_id)
        try:
            result = await asyncio.to_thread(orch.run, text, [])
            redacted, _ = redact_pii(result.get("response", ""))
            reply = redacted or "(no response)"
        finally:
            orch.close()
    except Exception as e:  # noqa: BLE001
        log.exception("slack background processing failed")
        reply = f"Sorry, I hit an error: {e}"

    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            await c.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {bot_token}"},
                json={"channel": channel, "text": reply},
            )
    except Exception:  # noqa: BLE001
        log.exception("slack chat.postMessage failed")


@app.post("/webhooks/slack")
async def slack_webhook(request: Request) -> Any:
    """Receives Slack Events API webhooks. Verifies the HMAC signature, ack's
    immediately (within Slack's 3s deadline), and processes the message in a
    background task — Render keeps the worker alive after the response, unlike
    Vercel where the function freezes the moment we ack."""
    raw = await request.body()
    timestamp = request.headers.get("x-slack-request-timestamp", "")
    signature = request.headers.get("x-slack-signature", "")

    # Slack's URL-verification handshake — sent once during app setup, before
    # the signing secret is in play. Echo the challenge so verification passes.
    try:
        body = _json.loads(raw.decode("utf-8") or "{}")
    except ValueError:
        body = {}
    if body.get("type") == "url_verification":
        return {"challenge": body.get("challenge", "")}

    ok, diag = _verify_slack_signature(timestamp, signature, raw)
    if not ok:
        # `secretFp` is a non-reversible fingerprint of the secret currently
        # loaded in this process. Compare it across deploys / between Slack
        # and Render to confirm a copy-paste mismatch without leaking the
        # secret itself. `retry` headers help spot Slack's automatic retries.
        log.warning(
            "slack-webhook rejected: %s (rawBodyLen=%d, ts=%s, secretFp=%s, retryNum=%s, retryReason=%s)",
            diag,
            len(raw),
            timestamp or "(none)",
            _secret_fingerprint(settings.SLACK_SIGNING_SECRET),
            request.headers.get("x-slack-retry-num", "0"),
            request.headers.get("x-slack-retry-reason", "-"),
        )
        raise HTTPException(status_code=401, detail="invalid signature")

    if body.get("type") != "event_callback":
        return {"ok": True}

    event = body.get("event") or {}
    if event.get("bot_id"):
        return {"ok": True}  # ignore loops from our own bot

    event_type = event.get("type")
    if event_type not in ("app_mention", "message"):
        return {"ok": True}

    platform_user = event.get("user") or ""
    text = event.get("text") or ""
    channel = event.get("channel") or ""
    if not platform_user or not channel:
        return {"ok": True}

    # Resolve Slack user → internal userId. If unlinked, post a help message
    # rather than silently dropping.
    try:
        internal_user_id = NodeClient.lookup_bot_mapping("slack", platform_user)
    except Exception:  # noqa: BLE001
        log.exception("bot-mapping lookup failed")
        internal_user_id = None

    if not internal_user_id:
        # Best-effort post — we don't have a botToken without a linked user.
        # The user will see nothing; this branch mainly logs for the operator.
        log.info("slack message from unlinked user platform_user=%s", platform_user)
        return {"ok": True}

    # Pull this user's stored Slack bot token from the Node backend.
    try:
        node = NodeClient(internal_user_id)
        try:
            conn = node.get_connection("slack") or {}
        finally:
            node.close()
    except Exception:  # noqa: BLE001
        log.exception("fetching slack connection failed")
        conn = {}

    bot_token = (conn.get("secrets") or {}).get("botToken")
    if not bot_token:
        log.warning("no slack botToken for user=%s", internal_user_id)
        return {"ok": True}

    # Fire-and-forget: ack now, process and reply after.
    asyncio.create_task(_process_slack_event(internal_user_id, channel, text, bot_token))
    return {"ok": True}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, claims: dict = Depends(require_user)) -> ChatResponse:
    if not settings.GEMINI_API_KEY:
        raise HTTPException(status_code=503, detail="GEMINI_API_KEY not configured")
    user_id = claims["userId"]
    user_id_ctx.set(user_id)

    # ---- Guardrails: input + rate limit + injection screen ----
    try:
        message = validate_message(req.message)
        history_raw = validate_history([{"role": h.role, "content": h.content} for h in req.history])
        check_rate_limit(user_id)
    except GuardrailViolation as gv:
        metrics.incr("guardrail_violations_total", code=gv.code)
        status = 429 if gv.code == "rate_limited" else 400
        raise HTTPException(status_code=status, detail=str(gv))

    injection = detect_injection(message)
    if injection:
        # Soft guardrail: log + flag, do not block. The agent's system prompt
        # already resists most injection; we surface the signal in metrics
        # and tool_calls so it shows up in the audit trail.
        metrics.incr("injection_detected_total")
        log.warning("injection_pattern_match", extra={"pattern": injection})

    log.info(
        "chat_start",
        extra={"history_len": len(history_raw), "msg_len": len(message)},
    )

    orch = Orchestrator(user_id=user_id, email=claims.get("email"))
    try:
        result = orch.run(message, history=history_raw)
        # ---- Output filter: redact PII from the final response ----
        redacted, n = redact_pii(result.get("response", ""))
        if n:
            metrics.incr("pii_redactions_total", value=n)
            log.info("pii_redactions", extra={"count": n})
        result["response"] = redacted
        result["pii_redactions"] = n
        metrics.incr("chats_total", status="ok")
        return ChatResponse(**result)
    except Exception:
        metrics.incr("chats_total", status="error")
        raise
    finally:
        orch.close()


@app.post("/chat/audio", response_model=ChatResponse)
async def chat_audio(request: Request, claims: dict = Depends(require_user)) -> ChatResponse:
    """Accepts an audio file, transcribes it via Gemini, and runs it through the orchestrator."""
    if not settings.GEMINI_API_KEY:
        raise HTTPException(status_code=503, detail="GEMINI_API_KEY not configured")
    
    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="No audio data provided")

    user_id = claims["userId"]
    user_id_ctx.set(user_id)
    check_rate_limit(user_id)

    # 1. Transcribe/Process audio via Gemini native API
    # Since we want to use audio, we'll use the native Gemini REST API instead of the OpenAI shim.
    try:
        async with httpx.AsyncClient() as client:
            # We'll use the Gemini 2.0 Flash model directly
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.GEMINI_MODEL}:generateContent?key={settings.GEMINI_API_KEY}"
            
            # Simple multimodal prompt: "Transcribe this audio"
            import base64
            audio_b64 = base64.b64encode(body).decode('utf-8')
            
            payload = {
                "contents": [{
                    "parts": [
                        {"text": "Transcription and Action: Output only the transcribed text of this voice memo. It is a command for a freelance assistant. Do not add comments, just the transcription."},
                        {"inline_data": {"mime_type": "audio/webm", "data": audio_b64}}
                    ]
                }]
            }
            
            res = await client.post(url, json=payload, timeout=30.0)
            res.raise_for_status()
            transcription = res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            
            log.info("audio_transcription", extra={"len": len(transcription), "text": transcription})
    except Exception as e:
        log.error(f"Audio processing failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to process audio: {str(e)}")

    # 2. Run the transcription through the Orchestrator
    orch = Orchestrator(user_id=user_id, email=claims.get("email"))
    try:
        result = orch.run(transcription, history=[])
        redacted, n = redact_pii(result.get("response", ""))
        result["response"] = redacted
        result["pii_redactions"] = n
        return ChatResponse(**result)
    finally:
        orch.close()


@app.post("/approvals/execute")
async def execute_approval(request: Request, claims: dict = Depends(require_user)) -> dict:
    """Executes a previously pending tool call after human approval.

    The Node side stores the FULLY-QUALIFIED tool name (e.g.
    `issues__create_linear_issue`) in the approval record, but each MCP server
    only knows the short name (`create_linear_issue`). We split on `__` and
    locate the matching server by `server_name`."""
    data = await request.json()
    tool_name = data.get("tool") or ""
    args = data.get("arguments") or {}

    if "__" not in tool_name:
        raise HTTPException(status_code=400, detail=f"malformed tool name: {tool_name!r}")
    server_name, short_name = tool_name.split("__", 1)

    user_id = claims["userId"]
    user_id_ctx.set(user_id)

    orch = Orchestrator(user_id=user_id, email=claims.get("email"))
    try:
        for srv in orch.servers:
            if srv.server_name != server_name:
                continue
            if short_name not in srv._tools:
                continue
            # Bypass the approval gate so the handler runs the real action.
            srv._approval_bypass = True
            try:
                # Filter out None args — handlers use defaults for omitted ones.
                cleaned = {k: v for k, v in args.items() if v is not None}
                result = srv._tools[short_name].handler(**cleaned)
                return {"success": True, "result": result}
            finally:
                srv._approval_bypass = False

        raise HTTPException(
            status_code=404,
            detail=f"Tool {tool_name!r} not found (looked for server={server_name!r}, tool={short_name!r})",
        )
    except HTTPException:
        raise
    except Exception as e:
        log.error("Approval execution failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        orch.close()


# ---- Proactive agents scheduler --------------------------------------------

def require_cron_secret(request: Request) -> None:
    """Auth dep for cron-only endpoints. The scheduler (GitHub Actions or
    any other cron) presents the shared secret via X-Cron-Secret. We refuse
    when the secret isn't configured at all rather than allow-by-default."""
    if not settings.CRON_SHARED_SECRET:
        raise HTTPException(status_code=503, detail="CRON_SHARED_SECRET not configured")
    presented = request.headers.get("x-cron-secret", "")
    if presented != settings.CRON_SHARED_SECRET:
        raise HTTPException(status_code=401, detail="invalid cron secret")


@app.post("/agents/run")
def run_proactive_agents(
    request: Request,
    user_id: str,
    email: str | None = None,
    _=Depends(require_cron_secret),
) -> dict:
    """Run the four proactive agents for one user. Called by the cron.

    Returns: { "user_id", "reports": [...] } — one entry per agent. Each
    report includes findings (full audit trail) + the count of notifications
    actually pushed."""
    user_id_ctx.set(user_id)
    log.info("agents_run_start", extra={"agent_count": len(ALL_AGENT_CLASSES)})
    node = NodeClient(user_id=user_id, email=email)
    reports = []
    try:
        for cls in ALL_AGENT_CLASSES:
            agent = cls(node)
            report = agent.run()
            reports.append(report.to_dict())
            metrics.incr("proactive_agent_runs_total", agent=cls.name, outcome="error" if report.error else "ok")
            metrics.incr("proactive_notifications_total", value=report.notifications_sent, agent=cls.name)
    finally:
        node.close()
    log.info("agents_run_done", extra={"reports": len(reports)})
    return {"user_id": user_id, "reports": reports}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8001, reload=True)
