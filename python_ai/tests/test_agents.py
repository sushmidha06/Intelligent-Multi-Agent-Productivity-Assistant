"""Unit tests for the proactive agents.

The agents call NodeClient methods. We use a `FakeNode` that returns
canned data, then assert each agent's findings + notification calls.
"""

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import pytest

from app.agents.anomaly_detector import (
    AnomalyDetectorAgent,
    OVERDUE_GRACE_DAYS,
    SILENCE_DAYS,
    BURNOUT_OFF_HOURS_MIN_COUNT,
)
from app.agents.inbox_triage import InboxTriageAgent
from app.agents.project_monitor import ProjectMonitorAgent, compute_health
from app.agents.recurring_workflows import RecurringWorkflowsAgent
from app.agents import ALL_AGENT_CLASSES


class FakeNode:
    """Minimal NodeClient stand-in that records notification pushes."""

    def __init__(self, *, projects=None, invoices=None, alerts=None, emails=None):
        self.user_id = "user-test"
        self._projects = projects or []
        self._invoices = invoices or []
        self._alerts = alerts or []
        self._emails = emails or []
        self.notifications: list[dict] = []

    def get_collection(self, name):
        return {
            "projects": self._projects,
            "invoices": self._invoices,
            "alerts": self._alerts,
        }.get(name, [])

    def get_email_bodies(self):
        return self._emails

    def push_notification(self, *, title, body, kind="info"):
        self.notifications.append({"title": title, "body": body, "kind": kind})
        return {"id": f"n-{len(self.notifications)}", "ok": True}

    def close(self):
        pass


# ---------- ProjectMonitor: health computation ----------

class TestComputeHealth:
    def test_healthy_project_scores_high(self):
        score, reasons = compute_health({
            "status": "active", "daysLeft": 60, "commits": 30, "budget": 1000, "spent": 200,
        })
        assert score >= 90
        assert reasons == []

    def test_deadline_passed_drops_score(self):
        score, reasons = compute_health({
            "status": "active", "daysLeft": -2, "commits": 10, "budget": 1000, "spent": 100,
        })
        assert score < 70
        assert any("deadline" in r for r in reasons)

    def test_no_commits_near_deadline(self):
        score, reasons = compute_health({
            "status": "active", "daysLeft": 14, "commits": 0, "budget": 1000, "spent": 0,
        })
        assert any("commit" in r.lower() for r in reasons)

    def test_over_budget(self):
        score, reasons = compute_health({
            "status": "active", "daysLeft": 30, "commits": 5, "budget": 1000, "spent": 1100,
        })
        assert any("budget" in r for r in reasons)

    def test_completed_project_skipped(self):
        score, reasons = compute_health({"status": "completed", "daysLeft": -100})
        assert score == 100
        assert "non-active" in reasons[0]


class TestProjectMonitorAgent:
    def test_no_projects_records_finding(self):
        node = FakeNode(projects=[])
        report = ProjectMonitorAgent(node).run()
        assert any(f.kind == "no_projects" for f in report.findings)
        assert node.notifications == []

    def test_at_risk_project_pushes_notification(self):
        node = FakeNode(projects=[
            {"id": "p1", "name": "Acme", "status": "active", "daysLeft": -5, "commits": 0, "budget": 1000, "spent": 1100},
        ])
        report = ProjectMonitorAgent(node).run()
        assert report.notifications_sent == 1
        assert "Acme" in node.notifications[0]["title"]

    def test_healthy_projects_dont_nudge(self):
        node = FakeNode(projects=[
            {"id": "p1", "name": "Healthy", "status": "active", "daysLeft": 60, "commits": 50, "budget": 1000, "spent": 100},
        ])
        report = ProjectMonitorAgent(node).run()
        assert report.notifications_sent == 0

    def test_multiple_at_risk_bundled_into_one_nudge(self):
        node = FakeNode(projects=[
            {"id": "p1", "name": "Bad1", "status": "active", "daysLeft": -1, "commits": 0, "budget": 100, "spent": 200},
            {"id": "p2", "name": "Bad2", "status": "active", "daysLeft": 0, "commits": 0, "budget": 100, "spent": 200},
        ])
        report = ProjectMonitorAgent(node).run()
        assert report.notifications_sent == 1
        assert "2" in node.notifications[0]["title"]

    def test_critical_health_uses_error_severity(self):
        node = FakeNode(projects=[
            {"id": "p1", "name": "Critical", "status": "active", "daysLeft": -10, "commits": 0, "budget": 100, "spent": 200},
        ])
        report = ProjectMonitorAgent(node).run()
        # Health should be 0, which is < 40 -> critical severity for finding, error kind for nudge
        finding = next(f for f in report.findings if f.kind == "project_health")
        assert finding.severity == "critical"
        assert node.notifications[0]["kind"] == "error"


# ---------- InboxTriageAgent ----------

class TestInboxTriageAgent:
    def test_empty_inbox_records_finding(self):
        node = FakeNode(emails=[])
        report = InboxTriageAgent(node).run()
        assert any(f.kind == "empty_inbox" for f in report.findings)
        assert node.notifications == []

    def test_no_api_key_short_circuits(self, monkeypatch):
        from app.agents import inbox_triage
        monkeypatch.setattr(inbox_triage.settings, "GEMINI_API_KEY", "")
        node = FakeNode(emails=[{"id": "e1", "subject": "x", "body": "y"}])
        report = InboxTriageAgent(node).run()
        assert any(f.kind == "disabled" for f in report.findings)

    def test_urgent_email_triggers_notification(self, monkeypatch):
        # Stub the LLM call.
        node = FakeNode(emails=[
            {"id": "e1", "subject": "API down NOW", "from": "Client <c@acme.com>", "body": "prod is down"},
            {"id": "e2", "subject": "Newsletter", "from": "News", "body": "weekly digest"},
        ])

        def fake_classify(self, emails):
            return [{"id": e["id"], "priority": "urgent" if i == 0 else "low", "reason": "test"}
                    for i, e in enumerate(emails)]

        monkeypatch.setattr(InboxTriageAgent, "_classify", fake_classify)
        report = InboxTriageAgent(node).run()
        assert report.notifications_sent == 1
        assert "urgent" in node.notifications[0]["title"].lower()

    def test_malformed_llm_json_falls_back_gracefully(self, monkeypatch):
        node = FakeNode(emails=[{"id": "e1", "subject": "x", "body": "y"}])
        # Simulating what _classify returns when it catches its own error
        def fake_classify_fallback(self, emails):
            return [{"priority": "normal", "reason": "triage unavailable"} for _ in emails]
        monkeypatch.setattr(InboxTriageAgent, "_classify", fake_classify_fallback)

        # Should not crash; should record findings as normal/info
        report = InboxTriageAgent(node).run()
        assert any(f.kind == "triage_normal" for f in report.findings)
        assert report.notifications_sent == 0
        assert report.error is None


# ---------- AnomalyDetectorAgent ----------

def _email(date: datetime, sender="Acme <ops@acme.com>", addr="ops@acme.com"):
    return {
        "id": f"e-{date.isoformat()}",
        "subject": "x",
        "from": sender,
        "fromAddress": addr,
        "date": format_datetime(date),
        "body": "",
    }


class TestAnomalyDetector:
    def test_silent_client_detected(self):
        old = datetime.now(timezone.utc) - timedelta(days=SILENCE_DAYS + 5)
        node = FakeNode(
            projects=[{"id": "p1", "name": "Acme Site", "client": "Acme", "status": "active"}],
            emails=[_email(old)],
        )
        report = AnomalyDetectorAgent(node).run()
        kinds = {f.kind for f in report.findings}
        assert "silent_client" in kinds
        assert any("Silent client" in n["title"] for n in node.notifications)

    def test_recent_email_not_silent(self):
        recent = datetime.now(timezone.utc) - timedelta(days=2)
        node = FakeNode(
            projects=[{"id": "p1", "name": "Acme", "client": "Acme", "status": "active"}],
            emails=[_email(recent)],
        )
        report = AnomalyDetectorAgent(node).run()
        kinds = {f.kind for f in report.findings}
        assert "silent_client" not in kinds

    def test_overdue_invoice_detected(self):
        past_due = (datetime.now(timezone.utc) - timedelta(days=OVERDUE_GRACE_DAYS + 5)).isoformat()
        node = FakeNode(invoices=[
            {"id": "i1", "client": "Acme", "amount": 500, "status": "unpaid", "dueDate": past_due},
        ])
        report = AnomalyDetectorAgent(node).run()
        assert any(f.kind == "overdue_invoice" for f in report.findings)

    def test_paid_invoice_ignored(self):
        past_due = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        node = FakeNode(invoices=[
            {"id": "i1", "client": "Acme", "amount": 500, "status": "paid", "dueDate": past_due},
        ])
        report = AnomalyDetectorAgent(node).run()
        assert not any(f.kind == "overdue_invoice" for f in report.findings)

    def test_burnout_signal_fires(self):
        # Pile up off-hours email events — Saturdays count as off-hours.
        base = datetime.now(timezone.utc) - timedelta(days=2)
        # Force a weekend timestamp
        while base.weekday() not in {5, 6}:
            base -= timedelta(days=1)
        emails = [_email(base + timedelta(hours=i)) for i in range(BURNOUT_OFF_HOURS_MIN_COUNT + 2)]
        node = FakeNode(emails=emails)
        report = AnomalyDetectorAgent(node).run()
        assert any(f.kind == "burnout_risk" for f in report.findings)

    def test_scope_creep_detected(self):
        node = FakeNode(projects=[
            {"id": "p1", "name": "Acme", "client": "Acme", "status": "active", "budget": 1000, "spent": 950},
        ])
        report = AnomalyDetectorAgent(node).run()
        assert any(f.kind == "scope_creep" for f in report.findings)

    def test_under_budget_no_creep(self):
        node = FakeNode(projects=[
            {"id": "p1", "name": "Acme", "client": "Acme", "status": "active", "budget": 1000, "spent": 100},
        ])
        report = AnomalyDetectorAgent(node).run()
        assert not any(f.kind == "scope_creep" for f in report.findings)

    def test_multiple_overdue_invoices_summary_body(self):
        past_due = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        node = FakeNode(invoices=[
            {"id": "i1", "client": "Acme", "amount": 500, "status": "unpaid", "dueDate": past_due},
            {"id": "i2", "client": "Stark", "amount": 1000, "status": "unpaid", "dueDate": past_due},
        ])
        report = AnomalyDetectorAgent(node).run()
        assert report.notifications_sent == 1
        assert "2" in node.notifications[0]["title"]
        assert "1500" in node.notifications[0]["body"]  # Total amount


# ---------- RecurringWorkflowsAgent ----------

class TestRecurringWorkflows:
    def test_out_of_window_no_nudge(self):
        # Pick a Wednesday at 3pm — neither Monday morning nor 1st of month.
        when = datetime(2026, 4, 22, 15, 0, tzinfo=timezone.utc)
        node = FakeNode(projects=[{"id": "p1", "name": "x", "status": "active"}])
        agent = RecurringWorkflowsAgent(node)
        agent._run(now=when)
        assert agent.report.notifications_sent == 0
        assert any(f.kind == "out_of_window" for f in agent.report.findings)

    def test_weekly_window_fires(self):
        # 2026-04-27 is a Monday at 9am UTC.
        when = datetime(2026, 4, 27, 9, 0, tzinfo=timezone.utc)
        assert when.weekday() == 0
        node = FakeNode(
            projects=[{"id": "p1", "name": "x", "status": "active", "commits": 5}],
            invoices=[{"id": "i1", "status": "unpaid"}],
        )
        agent = RecurringWorkflowsAgent(node)
        agent._run(now=when)
        assert agent.report.notifications_sent == 1
        assert any(f.kind == "weekly_summary" for f in agent.report.findings)

    def test_monthly_window_fires(self):
        # 1st of month at 9am.
        when = datetime(2026, 5, 1, 9, 0, tzinfo=timezone.utc)
        node = FakeNode(projects=[{"id": "p1", "name": "x", "status": "active"}])
        agent = RecurringWorkflowsAgent(node)
        agent._run(now=when)
        assert any(f.kind == "monthly_invoice_reminder" for f in agent.report.findings)

    def test_window_boundary_exact_end_does_not_fire(self):
        # Window is [8, 10), so 10:00:00 should NOT fire.
        when = datetime(2026, 4, 27, 10, 0, tzinfo=timezone.utc)
        node = FakeNode(projects=[{"id": "p1", "status": "active"}])
        agent = RecurringWorkflowsAgent(node)
        agent._run(now=when)
        assert agent.report.notifications_sent == 0


# ---------- Base class plumbing ----------

class TestAgentBase:
    def test_failure_caught_and_recorded(self):
        node = FakeNode()

        class Boom(ProjectMonitorAgent):
            def _run(self):
                raise RuntimeError("test boom")

        report = Boom(node).run()
        assert report.error and "RuntimeError" in report.error

    def test_notify_failure_does_not_raise(self):
        node = FakeNode(projects=[{"id": "p1", "name": "x", "status": "active",
                                   "daysLeft": -1, "commits": 0, "budget": 100, "spent": 200}])

        def boom(*_, **__):
            raise RuntimeError("network down")
        node.push_notification = boom

        # Should complete without raising
        report = ProjectMonitorAgent(node).run()
        assert report.notifications_sent == 0
        assert report.error is None


class TestAllAgents:
    def test_all_classes_instantiable(self):
        node = FakeNode()
        for cls in ALL_AGENT_CLASSES:
            agent = cls(node)
            assert agent.name is not None
            assert hasattr(agent, "run")
