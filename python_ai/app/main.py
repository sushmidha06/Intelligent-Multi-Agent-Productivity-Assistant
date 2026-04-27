"""FastAPI entrypoint for the Sushmi MCP AI service.

- `/health` — liveness probe
- `/chat`   — accepts a user message, runs the MCP-backed LangChain agent,
              returns the answer + tool-call trace. Auth: service JWT from
              the Node backend (HS256, userId claim).
- `/mcp/servers` — debug endpoint: lists MCP servers + their tools (auth'd).
"""

from __future__ import annotations

import logging
from typing import Any

import traceback

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .agent import Orchestrator
from .security import require_user
from .settings import settings

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("sushmi.ai")

app = FastAPI(title="Sushmi MCP AI Service", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Only the Node backend calls this; it's already behind auth.
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def all_exceptions_handler(_: Request, exc: Exception) -> JSONResponse:
    # Return the actual error text so upstream logs / debuggers can see it.
    # For a server that's only reachable via the Node backend, this is safe.
    log.exception("unhandled error")
    return JSONResponse(
        status_code=500,
        content={
            "detail": f"{type(exc).__name__}: {exc}",
            "trace": traceback.format_exc().splitlines()[-8:],
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


@app.get("/")
def root() -> dict:
    return {
        "service": "sushmi-mcp-ai",
        "status": "running",
        "endpoints": {
            "health": "/health",
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
        "build": "v9-email-rag-2025-04-25",
    }


@app.get("/mcp/servers")
def list_mcp_servers(claims: dict = Depends(require_user)) -> dict:
    """Returns the MCP server/tool catalog for the current user — useful for
    verifying multi-tenancy and for the assignment write-up screenshot."""
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
    if not req.message or not req.message.strip():
        raise HTTPException(status_code=400, detail="empty message")
    orch = Orchestrator(user_id=claims["userId"], email=claims.get("email"))
    try:
        history = [{"role": h.role, "content": h.content} for h in req.history]
        log.info("chat userId=%s msg=%r history=%d", claims["userId"], req.message[:80], len(history))
        result = orch.run(req.message, history=history)
        return ChatResponse(**result)
    finally:
        orch.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8001, reload=True)
