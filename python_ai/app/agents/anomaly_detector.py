"""Anomaly Detector Agent.

Looks for four classes of issue, each tunable:

1. **Silent client** — a project's client hasn't appeared in any indexed
   email in the last `SILENCE_DAYS`. Strong signal that follow-up is needed.

2. **Overdue invoices** — `status` is unpaid AND `dueDate` is more than
   `OVERDUE_GRACE_DAYS` in the past. We flag each one but bundle the nudge.

3. **Burnout signal** — many emails sent / commits made outside business
   hours (before 8am or after 8pm local, or on weekends). Heuristic — uses
   email `date` from indexed emails as a stand-in for "user activity."

4. **Scope creep** — `spent / budget >= 0.9` while `status == 'active'`.
   This overlaps with ProjectMonitor's budget-burn signal but the framing
   is different (creep = scope, monitor = health), so we keep them separate.

The scheduler runs this every 30 min; each detector has its own dedupe-by-fact
logic so we don't spam.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from .base import ProactiveAgent


SILENCE_DAYS = 14
OVERDUE_GRACE_DAYS = 3
BURNOUT_OFF_HOURS_MIN_COUNT = 6  # in the last week
WEEKEND = {5, 6}                  # Sat=5, Sun=6


def _parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    # Try several formats — emails sometimes ship RFC 2822, sometimes ISO.
    for parser in (parsedate_to_datetime, datetime.fromisoformat):
        try:
            d = parser(s)
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            return d
        except (TypeError, ValueError):
            continue
    return None


def _is_off_hours(d: datetime) -> bool:
    if d.weekday() in WEEKEND:
        return True
    return d.hour < 8 or d.hour >= 20


class AnomalyDetectorAgent(ProactiveAgent):
    name = "anomaly-detector"

    def _run(self) -> None:
        projects = self.node.get_collection("projects") or []
        invoices = self.node.get_collection("invoices") or []
        emails = self.node.get_email_bodies() or []

        nudges_to_send: list[tuple[str, str, str]] = []

        nudges_to_send.extend(self._silent_clients(projects, emails))
        nudges_to_send.extend(self._overdue_invoices(invoices))
        nudges_to_send.extend(self._burnout_signal(emails))
        nudges_to_send.extend(self._scope_creep(projects))

        # Send up to one per category to avoid notification spam in a single run.
        # The findings array still records everything for the audit trail.
        for title, body, kind in nudges_to_send:
            self.notify(title=title, body=body, kind=kind)

    # ----- Detectors --------------------------------------------------------

    def _silent_clients(self, projects: list[dict], emails: list[dict]) -> list[tuple[str, str, str]]:
        if not projects:
            return []
        cutoff = datetime.now(timezone.utc) - timedelta(days=SILENCE_DAYS)
        # Group most-recent email date per sender domain or sender name.
        last_seen: dict[str, datetime] = {}
        for e in emails:
            d = _parse_date(e.get("date"))
            if not d:
                continue
            sender = (e.get("from") or "").lower()
            addr = (e.get("fromAddress") or "").lower()
            for key in (sender, addr):
                if key and (key not in last_seen or last_seen[key] < d):
                    last_seen[key] = d

        results: list[tuple[str, str, str]] = []
        for p in projects:
            client = (p.get("client") or "").lower().strip()
            if not client:
                continue
            if (p.get("status") or "").lower() in {"completed", "archived", "cancelled"}:
                continue
            # Treat "any sender containing the client name" as a match.
            most_recent = None
            for key, when in last_seen.items():
                if client in key:
                    if most_recent is None or when > most_recent:
                        most_recent = when
            if most_recent is None or most_recent < cutoff:
                days = (datetime.now(timezone.utc) - most_recent).days if most_recent else None
                body = (
                    f"No emails from {p.get('client')} in the last {SILENCE_DAYS} days"
                    if days is None
                    else f"Last email from {p.get('client')} was {days} days ago"
                )
                self.add_finding(
                    "silent_client",
                    title=f"{p.get('client')} has gone quiet",
                    body=body,
                    severity="warn",
                    project_id=p.get("id"),
                )
                results.append((f"Silent client: {p.get('client')}", body, "warn"))
        # Cap at 1 nudge per run for this category.
        return results[:1]

    def _overdue_invoices(self, invoices: list[dict]) -> list[tuple[str, str, str]]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=OVERDUE_GRACE_DAYS)
        overdue: list[dict] = []
        for inv in invoices:
            status = (inv.get("status") or "").lower()
            if status not in {"unpaid", "overdue", "sent", "pending"}:
                continue
            due = _parse_date(inv.get("dueDate"))
            if due and due < cutoff:
                overdue.append(inv)
                self.add_finding(
                    "overdue_invoice",
                    title=f"Invoice {inv.get('id')}",
                    body=f"{inv.get('client', 'Unknown')} — {inv.get('amount', '?')} due {inv.get('dueDate')}",
                    severity="warn",
                    invoice_id=inv.get("id"),
                )
        if not overdue:
            return []
        if len(overdue) == 1:
            inv = overdue[0]
            return [(
                f"Overdue invoice from {inv.get('client', 'client')}",
                f"{inv.get('amount', '?')} was due {inv.get('dueDate')}",
                "warn",
            )]
        total = sum(_safe_amount(i) for i in overdue)
        return [(
            f"{len(overdue)} overdue invoices",
            f"Total outstanding: {total}",
            "warn",
        )]

    def _burnout_signal(self, emails: list[dict]) -> list[tuple[str, str, str]]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        off_hours = 0
        for e in emails:
            d = _parse_date(e.get("date"))
            if not d or d < cutoff:
                continue
            if _is_off_hours(d):
                off_hours += 1
        if off_hours < BURNOUT_OFF_HOURS_MIN_COUNT:
            return []
        body = (
            f"{off_hours} email events in the last week were outside business hours "
            f"(weekends or before 8am / after 8pm). Consider blocking time off."
        )
        self.add_finding("burnout_risk", "Possible burnout pattern", body, severity="warn", off_hours_count=off_hours)
        return [("You're working late a lot", body, "warn")]

    def _scope_creep(self, projects: list[dict]) -> list[tuple[str, str, str]]:
        flagged: list[dict] = []
        for p in projects:
            if (p.get("status") or "").lower() not in {"active", "in-progress", "in_progress", ""}:
                continue
            try:
                budget = float(p.get("budget") or 0)
                spent = float(p.get("spent") or 0)
            except (TypeError, ValueError):
                continue
            if budget <= 0:
                continue
            if spent / budget >= 0.9:
                flagged.append(p)
                self.add_finding(
                    "scope_creep",
                    title=p.get("name", "Project"),
                    body=f"Spent {spent}/{budget} ({int(spent / budget * 100)}%)",
                    severity="warn",
                    project_id=p.get("id"),
                )
        if not flagged:
            return []
        if len(flagged) == 1:
            p = flagged[0]
            return [(
                f"Scope creep: {p.get('name')}",
                f"Spent {p.get('spent')}/{p.get('budget')} on {p.get('client', '?')}",
                "warn",
            )]
        return [(
            f"{len(flagged)} projects near or over budget",
            "Review scope vs. spend before billing more time.",
            "warn",
        )]


def _safe_amount(inv: dict) -> float:
    try:
        return float(inv.get("amount") or 0)
    except (TypeError, ValueError):
        return 0.0
