"""Base class + shared types for the proactive-agent family.

Design:
- Each agent receives a `NodeClient` (already scoped to a user) at construction.
- `run()` does the work and returns an `AgentReport` describing what was found.
- Agents may push notifications via `self.notify(...)` — the base class handles
  the HTTP call back into Node, the agent only decides *whether* to nudge.
- Agents must be **idempotent within a short window**. The scheduler may
  invoke them every 30 min; sending the same notification 48× a day is worse
  than sending none. Each agent decides its own dedupe window.

This is the third agent role in the system:
  1. Planner    — chat-time, breaks user request into a plan
  2. Executor   — chat-time, runs the plan with MCP tools
  3. ProactiveAgent (this) — scheduled, runs without a user prompt
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from ..node_client import NodeClient


log = logging.getLogger("sushmi.agents")


@dataclass
class AgentFinding:
    """One thing the agent noticed worth surfacing."""
    kind: str                      # e.g., "urgent_email", "stale_client"
    title: str                     # short, user-facing
    body: str                      # one-paragraph explanation
    severity: str = "info"         # "info" | "warn" | "critical"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentReport:
    """Summary of one agent run. Returned to the scheduler so it can log
    and (optionally) display in the admin UI."""
    agent: str
    user_id: str
    findings: list[AgentFinding] = field(default_factory=list)
    notifications_sent: int = 0
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "agent": self.agent,
            "user_id": self.user_id,
            "findings": [
                {"kind": f.kind, "title": f.title, "body": f.body, "severity": f.severity}
                for f in self.findings
            ],
            "notifications_sent": self.notifications_sent,
            "error": self.error,
        }


class ProactiveAgent:
    """Subclass and override `run()`. Use `self.add_finding()` and
    `self.notify()` to record/escalate observations."""

    name: str = "proactive-agent"

    def __init__(self, node: NodeClient):
        self.node = node
        self.report = AgentReport(agent=self.name, user_id=node.user_id)

    def add_finding(self, kind: str, title: str, body: str, severity: str = "info", **metadata) -> AgentFinding:
        f = AgentFinding(kind=kind, title=title, body=body, severity=severity, metadata=metadata)
        self.report.findings.append(f)
        return f

    def notify(self, title: str, body: str, kind: str = "info") -> bool:
        """Push an in-app notification via the Node backend.
        Returns True on success. Failures are logged but never raise — a
        failed notification should not break the agent run."""
        try:
            self.node.push_notification(title=title, body=body, kind=kind)
            self.report.notifications_sent += 1
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("notify failed for %s: %s", self.name, e)
            return False

    def run(self) -> AgentReport:
        try:
            self._run()
        except Exception as e:  # noqa: BLE001
            log.exception("%s failed for user=%s", self.name, self.node.user_id)
            self.report.error = f"{type(e).__name__}: {e}"
        return self.report

    def _run(self) -> None:
        raise NotImplementedError
