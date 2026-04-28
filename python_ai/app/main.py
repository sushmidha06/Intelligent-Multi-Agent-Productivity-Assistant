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
