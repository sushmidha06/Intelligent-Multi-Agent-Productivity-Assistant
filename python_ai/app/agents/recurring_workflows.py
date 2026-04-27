"""Recurring Workflows Agent.

Generates time-based nudges that don't come from any one signal:

- **Weekly summary** — every Monday morning (UTC). Counts active projects,
  unpaid invoices, recent commits; sends a single notification with the
  digest.

- **Monthly invoicing reminder** — on the 1st of each month, nudge the
  user to send invoices for the prior month's billable work.

The scheduler runs this agent every 30 min along with the others. Each
sub-task checks the calendar before firing, so they only actually push
their nudge once per scheduled window.

Idempotency: the dedupe is *temporal* — "is it Monday between 8-10am UTC?"
The notifications layer doesn't have a "did we already send this today?"
flag, so the trade-off is simple: keep the firing window narrow (2 hours)
and run the cron at 30-min intervals so you fire 4× max per week per user.
The Node notification layer can dedupe by title if needed.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .base import ProactiveAgent


WEEKLY_DAY = 0     # Monday
WEEKLY_HOUR_START = 8
WEEKLY_HOUR_END = 10

MONTHLY_DAY = 1    # 1st of the month
MONTHLY_HOUR_START = 8
MONTHLY_HOUR_END = 10


class RecurringWorkflowsAgent(ProactiveAgent):
    name = "recurring-workflows"

    def _run(self, *, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)

        if WEEKLY_HOUR_START <= now.hour < WEEKLY_HOUR_END and now.weekday() == WEEKLY_DAY:
            self._weekly_summary()
        if MONTHLY_HOUR_START <= now.hour < MONTHLY_HOUR_END and now.day == MONTHLY_DAY:
            self._monthly_invoice_reminder()

        if not self.report.findings:
            self.add_finding(
                "out_of_window",
                "Nothing scheduled now",
                "Recurring workflows only fire on Monday mornings and the 1st of the month (UTC).",
                severity="info",
            )

    # ----- Workflows --------------------------------------------------------

    def _weekly_summary(self) -> None:
        projects = self.node.get_collection("projects") or []
        invoices = self.node.get_collection("invoices") or []
        active = sum(1 for p in projects if (p.get("status") or "").lower() in {"active", "in-progress", "in_progress", ""})
        unpaid = sum(1 for i in invoices if (i.get("status") or "").lower() in {"unpaid", "overdue", "sent", "pending"})
        commits = sum(int(p.get("commits") or 0) for p in projects)

        body = (
            f"Active projects: {active}. Unpaid invoices: {unpaid}. "
            f"Total commits across projects: {commits}."
        )
        self.add_finding(
            "weekly_summary",
            "Weekly snapshot",
            body,
            severity="info",
            active_projects=active,
            unpaid_invoices=unpaid,
            total_commits=commits,
        )
        self.notify(title="Your week ahead", body=body, kind="info")

    def _monthly_invoice_reminder(self) -> None:
        projects = self.node.get_collection("projects") or []
        active = [p for p in projects if (p.get("status") or "").lower() in {"active", "in-progress", "in_progress", ""}]
        if not active:
            return
        body = (
            f"It's the 1st — time to invoice last month's work. "
            f"You have {len(active)} active projects."
        )
        self.add_finding(
            "monthly_invoice_reminder",
            "Time to invoice last month",
            body,
            severity="info",
            active_projects=len(active),
        )
        self.notify(title="Send your monthly invoices", body=body, kind="info")
