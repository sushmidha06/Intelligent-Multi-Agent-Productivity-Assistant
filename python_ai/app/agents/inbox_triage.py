"""Inbox Triage Agent.

Reads the user's most-recent indexed emails, asks Gemini to label each one
as urgent / normal / low with a one-line reason, and pushes a single grouped
notification when there are urgent items.

Why a single grouped notification: pushing one nudge per urgent email
floods the bell. Users tune that out fast. One "you have 3 urgent emails"
is actionable.

Cost shape: one Gemini call per run, regardless of inbox size.
"""

from __future__ import annotations

import json
import logging

import httpx

from ..settings import settings
from .base import ProactiveAgent


log = logging.getLogger("sushmi.agents.inbox")


GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"

TRIAGE_SYSTEM = """You are an email-triage assistant for a freelance professional.
Given a list of emails (subject + sender + first 400 chars of body), label each
one with a priority. Return a JSON array of objects, one per input email, in the
SAME ORDER as the input. Each object has:

  {"id": "<echo input id>", "priority": "urgent" | "normal" | "low", "reason": "<one short sentence>"}

Definitions:
  - urgent: needs a reply within 24h; client deadline / payment dispute / outage / explicit "ASAP"
  - normal: useful but no rush; status update / scheduling / general discussion
  - low: marketing / newsletter / automated notification / receipt

Output JSON ONLY. No markdown fences, no preamble."""


class InboxTriageAgent(ProactiveAgent):
    name = "inbox-triage"

    # How many emails to triage per run. The Gemini call cost scales linearly
    # so we cap at the most-recent 20 — enough to catch anything fresh.
    BATCH_SIZE = 20

    def _run(self) -> None:
        if not settings.GEMINI_API_KEY:
            self.add_finding("disabled", "Triage skipped", "GEMINI_API_KEY not set", severity="info")
            return

        emails = (self.node.get_email_bodies() or [])[: self.BATCH_SIZE]
        if not emails:
            self.add_finding("empty_inbox", "Nothing to triage", "No indexed emails — sync your inbox first.", severity="info")
            return

        labels = self._classify(emails)
        urgent = [(e, l) for e, l in zip(emails, labels) if l.get("priority") == "urgent"]

        for e, l in zip(emails, labels):
            self.add_finding(
                kind=f"triage_{l.get('priority', 'normal')}",
                title=(e.get("subject") or "(no subject)")[:100],
                body=l.get("reason", ""),
                severity="warn" if l.get("priority") == "urgent" else "info",
                email_id=e.get("id") or e.get("uid"),
                priority=l.get("priority"),
            )

        if urgent:
            top = urgent[0][0]
            sender = (top.get("from") or top.get("fromAddress") or "Unknown sender")[:60]
            subject = (top.get("subject") or "(no subject)")[:80]
            if len(urgent) == 1:
                title = "1 urgent email needs a reply"
                body = f'"{subject}" — from {sender}'
            else:
                title = f"{len(urgent)} urgent emails need a reply"
                body = f'Top: "{subject}" — from {sender}'
            self.notify(title=title, body=body, kind="warn")

    def _classify(self, emails: list[dict]) -> list[dict]:
        """One LLM call, returns one label dict per input email (same order)."""
        # Compact payload — only what the model needs to triage.
        payload_emails = []
        for i, e in enumerate(emails):
            payload_emails.append({
                "id": str(e.get("id") or e.get("uid") or i),
                "subject": (e.get("subject") or "")[:200],
                "from": (e.get("from") or e.get("fromAddress") or "")[:120],
                "body": (e.get("body") or "")[:400],
            })
        try:
            resp = httpx.post(
                GEMINI_URL,
                headers={"Authorization": f"Bearer {settings.GEMINI_API_KEY}"},
                json={
                    "model": settings.GEMINI_MODEL,
                    "messages": [
                        {"role": "system", "content": TRIAGE_SYSTEM},
                        {"role": "user", "content": json.dumps({"emails": payload_emails})},
                    ],
                    "temperature": 0.1,
                },
                timeout=30.0,
            )
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"].strip()
            # Strip code fences if the model added them despite our instruction.
            if text.startswith("```"):
                text = text.strip("`").lstrip("json").strip()
            data = json.loads(text)
            if isinstance(data, dict) and "emails" in data:
                data = data["emails"]
            if not isinstance(data, list):
                raise ValueError("expected JSON array")
            # Pad/truncate to len(emails) — defensive against the model returning the wrong count.
            out = list(data)[: len(emails)]
            while len(out) < len(emails):
                out.append({"priority": "normal", "reason": "model omitted label"})
            return out
        except Exception as e:  # noqa: BLE001
            log.warning("triage classify failed: %s", e)
            return [{"priority": "normal", "reason": "triage unavailable"} for _ in emails]
