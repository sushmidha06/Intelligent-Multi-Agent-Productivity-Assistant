"""Planner agent — turns the user's request into a short plan that the
executor agent (the existing tool-calling AgentExecutor) follows.

This is the second agent in the multi-agent system. Architecture:

    user message ─► [Planner LLM] ─► plan (1-5 steps) ─► [Executor agent] ─► answer

The planner sees:
  - The user's message + chat history
  - The list of tools available (names + one-line descriptions)
  - No actual tool execution capability — it can only think

The executor sees:
  - The original user message
  - The plan as a system-level hint
  - Full tool access via MCP

Why this is worth the extra LLM call:
  - For complex multi-step asks ("find meeting requests in email and add
    to calendar"), the executor stays on track better when given a plan.
  - It gives us a clean place to enforce guardrails on the *intent* of
    the request before any tool fires.
  - Plans are logged — gives us a `tool_calls`-style audit trail of the
    agent's reasoning, not just its actions.

For trivial asks (under `PLAN_SKIP_THRESHOLD` chars) we skip the planner
to keep latency down — a one-line "what time is it?" doesn't need a plan.
"""

from __future__ import annotations

import logging

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from .settings import settings


log = logging.getLogger("sushmi.planner")


PLAN_SKIP_THRESHOLD = 60  # chars; below this, skip the planner

PLANNER_SYSTEM = """You are the **Planner** agent inside Sushmi, a freelance ops copilot.

Your job is NOT to answer the user. Your job is to break down their request
into a short, ordered plan (max 5 steps) for the **Executor** agent to follow.

The Executor has these tools:
{tools_summary}

Rules for your plan:
- 1-5 numbered steps. Concise — one short imperative sentence per step.
- Each step should map to at most one tool call OR one synthesis step.
- If the request is trivial ("hi", "what can you do") respond with a single step:
  "1. Reply directly without tools."
- Do not include data the Executor will fetch — the plan is structure, not content.
- Do not greet, apologize, or add commentary. Output ONLY the numbered steps."""


class Planner:
    """Tiny single-LLM-call planner. Runs before the Executor."""

    def __init__(self, tools: list):
        # Build a compact tool summary the planner can reason over without
        # blowing the prompt. We keep it to "name — first line of description".
        lines = []
        for t in tools:
            desc = (getattr(t, "description", "") or "").strip().splitlines()
            first = desc[0] if desc else ""
            lines.append(f"- {t.name}: {first}")
        self.tools_summary = "\n".join(lines) or "(no tools)"
        self.llm = ChatOpenAI(
            model=settings.GEMINI_MODEL,
            api_key=settings.GEMINI_API_KEY,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            temperature=0.0,
            timeout=20.0,
            max_retries=1,
        )

    def plan(self, message: str, history: list | None = None) -> str:
        """Return the plan as a string. Empty string => caller should skip planning."""
        if len(message) < PLAN_SKIP_THRESHOLD:
            return ""
        sys = PLANNER_SYSTEM.format(tools_summary=self.tools_summary)
        msgs: list = [SystemMessage(content=sys)]
        # The planner only needs the most recent turn for context — passing
        # full history makes it slower and rarely changes the plan.
        if history:
            recent = history[-2:]
            for h in recent:
                role = (h.get("role") or "user").lower() if isinstance(h, dict) else "user"
                content = h.get("content", "") if isinstance(h, dict) else str(h)
                if role == "assistant":
                    msgs.append(SystemMessage(content=f"[prev assistant]: {content[:300]}"))
                else:
                    msgs.append(SystemMessage(content=f"[prev user]: {content[:300]}"))
        msgs.append(HumanMessage(content=message))
        try:
            resp = self.llm.invoke(msgs)
            text = (resp.content or "").strip() if hasattr(resp, "content") else str(resp).strip()
            return text
        except Exception as e:  # noqa: BLE001
            # Planner failure is non-fatal — executor can still run without it.
            log.warning("planner failed: %s", e)
            return ""
