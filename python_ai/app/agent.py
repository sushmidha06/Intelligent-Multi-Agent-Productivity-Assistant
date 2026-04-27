"""Sushmi MCP agent orchestrator.

Pulls together:
  - NodeClient (scoped to a single userId) — enforces multi-tenancy
  - Four MCP servers (firestore, github, gmail, razorpay, knowledge_base)
  - LangChain `AgentExecutor` driving Gemini with tool-calling
  - RAG index snapshot built per-request from the user's Firestore data

The Gemini model plans → calls MCP tools via LangChain → sees results →
iterates until it has an answer. Hard-capped at AGENT_MAX_ITERATIONS.
"""

from __future__ import annotations

import time
from typing import Any

from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import AIMessage, HumanMessage
from langchain_openai import ChatOpenAI

from .mcp_langchain import mcp_server_to_langchain_tools
from .observability import metrics
from .planner import Planner
from .mcp_servers.calendar_server import CalendarMcpServer
from .mcp_servers.expenses_server import ExpensesMcpServer
from .mcp_servers.firestore_server import FirestoreMcpServer
from .mcp_servers.github_server import GithubMcpServer
from .mcp_servers.gmail_server import GmailMcpServer
from .mcp_servers.razorpay_server import RazorpayMcpServer
from .mcp_servers.rag_server import RagMcpServer
from .node_client import NodeClient
from .rag import RagIndex, build_docs_from_firestore, build_docs_from_emails
from .settings import settings


SYSTEM_PROMPT = """You are Sushmi, a proactive multi-agent freelance operations copilot.

You have access to MCP (Model Context Protocol) tools that let you act on the user's own data:
- `firestore__*`      — their projects, invoices, alerts inside this app
- `github__*`         — their GitHub repos, PRs, and commit activity
- `gmail__*`          — their Gmail: list_recent_emails, search_emails, get_email_body
- `calendar__*`       — their Google Calendar: list_upcoming_events, search_events, draft_event (one-click prefill URL)
- `razorpay__*`       — their Razorpay invoices and payments
- `expenses__create`  — log an expense (vendor, amount, date, category, optional project_id)
- `knowledge_base__search_knowledge` — semantic search over the user's workspace
  (projects + invoices + alerts) AND their **indexed Gmail inbox** if they've
  hit "Sync inbox" on the Inbox page. Use `source: "email"` to scope to inbox only.
  Prefer this for open-ended questions like "what did Acme say about the API last week?"

# How to behave

You are **agentic**. When the user gives you a multi-step goal, do not stop after one tool call —
chain tools together until you've actually accomplished the goal. Examples:

- "Find any meeting requests in my emails this week and add them to my calendar":
  1. `gmail__search_emails(query="meeting OR call OR schedule newer_than:7d")` to find candidates
  2. For each promising email, call `gmail__get_email_body(uid=...)` to read the full text
  3. Yourself extract the title, date, time, attendees, location from the body
  4. Call `calendar__draft_event(title=..., start=..., end=..., attendees=...)` for each one
  5. Return the prefill URLs in the chat with a short summary — the user clicks once to save each

- "What PRs are blocking me?":
  1. `github__list_open_prs(filter="review-requested")`
  2. Optionally `github__list_recent_commits` on the affected repos to see if they're stale
  3. Synthesize a short "X PRs need your attention, oldest is N days" answer

- "Log my Vercel receipt from yesterday as a hosting expense for the Northwind project":
  1. `gmail__search_emails(query="vercel newer_than:2d")` to find the receipt
  2. `gmail__get_email_body(uid=...)` to read amount + date
  3. `firestore__list_projects` to get Northwind's project_id
  4. `expenses__create(vendor="Vercel", amount=..., category="Hosting & infra", project_id=...)` to log it
  5. Confirm in chat with the new expense id and the project's updated spent

- "Summarise my week":
  1. `firestore__get_dashboard_summary` for the numbers
  2. `gmail__list_recent_emails(limit=10)` for inbox volume
  3. `calendar__list_upcoming_events(days=7)` for what's coming up
  4. Compose a single short brief

# Rules

- **Be willing to extract structured data from email bodies yourself.** You are a capable LLM —
  if a client email says "let's meet Friday at 3pm", you can interpret that into ISO-8601
  for `calendar__draft_event`. Don't refuse or ask the user to give you the details verbatim.
- **Default time zone is the user's preference** (UTC if unknown). For relative dates ("Friday",
  "next week"), compute against today's date.
- If a tool errors with "not connected", tell the user which integration to enable and stop.
- If you draft calendar events, return the URLs as clickable links with a one-line description
  per event. Don't dump the JSON.
- Never invent data. If you can't find what you need, say so plainly.
- Be concise. Bullets over paragraphs. Cite the tool you used briefly.
"""


class AgentResult(dict):
    pass


# Per-user RAG cache. Key: user_id. Value: (doc_signature, RagIndex).
# Reusing the index across requests skips re-embedding (the dominant chat-latency cost).
# The signature is a hash of the doc IDs+text — when the user's data changes, the hash
# changes and we rebuild automatically. No TTL needed.
_RAG_CACHE: dict[str, tuple[str, RagIndex]] = {}


def _doc_signature(docs: list) -> str:
    import hashlib
    h = hashlib.md5()
    for d in docs:
        h.update(d.id.encode())
        h.update(b"\x00")
        h.update(d.text.encode())
        h.update(b"\x01")
    return h.hexdigest()


class Orchestrator:
    def __init__(self, user_id: str, email: str | None = None):
        self.user_id = user_id
        self.node = NodeClient(user_id, email)

        # Build per-request RAG index from the user's workspace + indexed inbox.
        try:
            projects = self.node.get_collection("projects")
            invoices = self.node.get_collection("invoices")
            alerts   = self.node.get_collection("alerts")
        except Exception:
            projects, invoices, alerts = [], [], []
        try:
            email_bodies = self.node.get_email_bodies()
            # Cap to keep cold-cache embed under Vercel's 58s proxy budget.
            # Most-recent N emails, body truncated — still gives the agent
            # plenty of context for "what did people email me about" queries.
            email_bodies = email_bodies[:30]
            for e in email_bodies:
                if isinstance(e.get("body"), str) and len(e["body"]) > 2000:
                    e["body"] = e["body"][:2000]
        except Exception:
            email_bodies = []
        docs = build_docs_from_firestore(projects, invoices, alerts) + build_docs_from_emails(email_bodies)

        sig = _doc_signature(docs)
        cached = _RAG_CACHE.get(user_id)
        if cached and cached[0] == sig:
            self.rag_index = cached[1]
        else:
            self.rag_index = RagIndex(user_id, docs)
            _RAG_CACHE[user_id] = (sig, self.rag_index)

        # Spin up all MCP servers scoped to this user
        self.servers = [
            FirestoreMcpServer(self.node),
            GithubMcpServer(self.node),
            GmailMcpServer(self.node),
            CalendarMcpServer(self.node),
            RazorpayMcpServer(self.node),
            ExpensesMcpServer(self.node),
            RagMcpServer(self.rag_index),
        ]

        # Flatten into LangChain tools
        self.tools = []
        for srv in self.servers:
            self.tools.extend(mcp_server_to_langchain_tools(srv))

        # Gemini via its OpenAI-compatible endpoint. We deliberately avoid
        # `langchain-google-genai`'s ChatGoogleGenerativeAI because its
        # underlying `google-api-core` REST error parser crashes on certain
        # 429 / not-found responses ('list' object has no attribute 'get'),
        # which is unrecoverable from inside the agent loop. The OpenAI-style
        # endpoint returns standard JSON errors that LangChain handles cleanly.
        self.llm = ChatOpenAI(
            model=settings.GEMINI_MODEL,
            api_key=settings.GEMINI_API_KEY,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            temperature=0.2,
            timeout=45.0,
            max_retries=2,
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", SYSTEM_PROMPT),
                MessagesPlaceholder(variable_name="chat_history", optional=True),
                ("human", "{input}"),
                MessagesPlaceholder(variable_name="agent_scratchpad"),
            ]
        )
        agent = create_tool_calling_agent(self.llm, self.tools, prompt)
        self.executor = AgentExecutor(
            agent=agent,
            tools=self.tools,
            max_iterations=settings.AGENT_MAX_ITERATIONS,
            verbose=False,
            return_intermediate_steps=True,
            handle_parsing_errors=True,
        )

        # Second agent in the multi-agent system. Lazy: only instantiated
        # on the first run() call, since trivial messages skip planning.
        self._planner: Planner | None = None

    def _get_planner(self) -> Planner:
        if self._planner is None:
            self._planner = Planner(self.tools)
        return self._planner

    def run(self, message: str, history: list[dict] | None = None) -> AgentResult:
        lc_history = []
        for h in history or []:
            role = (h.get("role") or "user").lower()
            content = h.get("content") or ""
            if role == "assistant":
                lc_history.append(AIMessage(content=content))
            else:
                lc_history.append(HumanMessage(content=content))

        # ---- Multi-agent step 1: Planner ----
        plan_text = ""
        plan_start = time.monotonic()
        try:
            plan_text = self._get_planner().plan(message, history=history)
        except Exception:  # noqa: BLE001 — planner is non-fatal
            plan_text = ""
        plan_elapsed = time.monotonic() - plan_start
        if plan_text:
            metrics.observe("planner_duration_seconds", plan_elapsed)
            metrics.incr("planner_invocations_total", outcome="ok")
        else:
            metrics.incr("planner_invocations_total", outcome="skipped")

        # ---- Multi-agent step 2: Executor ----
        # If we have a plan, prepend it to the input so the executor sees it
        # as authoritative context. The system prompt already covers the rest.
        executor_input = message
        if plan_text:
            executor_input = (
                f"[Plan from Planner agent — follow these steps in order]\n"
                f"{plan_text}\n\n"
                f"[User's original message]\n{message}"
            )

        # Gemini free-tier sometimes 429s on burst. Retry a couple of times
        # with modest backoff. Total ~19s — well under the 55s upstream cap.
        result = None
        delays = [4, 6, 9]
        exec_start = time.monotonic()
        for i in range(len(delays) + 1):
            try:
                result = self.executor.invoke({"input": executor_input, "chat_history": lc_history})
                break
            except Exception as e:  # noqa: BLE001 — only retry rate-limit-shaped errors
                msg = str(e).lower()
                if "rate" in msg or "429" in msg or "quota" in msg or "resource" in msg:
                    if i == len(delays):
                        metrics.incr("executor_invocations_total", outcome="rate_limited")
                        raise
                    time.sleep(delays[i])
                else:
                    metrics.incr("executor_invocations_total", outcome="error")
                    raise
        exec_elapsed = time.monotonic() - exec_start
        metrics.observe("executor_duration_seconds", exec_elapsed)
        metrics.incr("executor_invocations_total", outcome="ok")

        tool_calls = self._extract_tool_calls(result.get("intermediate_steps") or [])
        for tc in tool_calls:
            if tc.get("tool"):
                metrics.incr("tool_calls_total", tool=tc["tool"])
        return AgentResult(
            response=result.get("output", ""),
            tool_calls=tool_calls,
            tools_available=[t.name for t in self.tools],
            plan=plan_text or None,
        )

    @staticmethod
    def _extract_tool_calls(steps: list[Any]) -> list[dict]:
        out = []
        for step in steps:
            action, observation = step if isinstance(step, tuple) and len(step) == 2 else (step, None)
            tool_name = getattr(action, "tool", None) if action is not None else None
            tool_input = getattr(action, "tool_input", None) if action is not None else None
            out.append({
                "tool": tool_name,
                "input": tool_input,
                "output": str(observation)[:800] if observation is not None else "",
            })
        return out

    def close(self):
        self.node.close()
