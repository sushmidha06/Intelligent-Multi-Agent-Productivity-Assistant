"""Tests for the human-in-the-loop approval gate.

The base McpServer's `_gate_with_approval` is the single chokepoint that
mutating tools route through. We exercise it via the real servers (expenses,
issues, documents, timesheets) using a fake NodeClient that records what
would have been enqueued/executed."""

from app.mcp_servers.documents_server import DocumentsMcpServer
from app.mcp_servers.expenses_server import ExpensesMcpServer
from app.mcp_servers.issue_tracker_server import IssueTrackerMcpServer
from app.mcp_servers.timesheet_server import TimesheetsMcpServer


class FakeNode:
    user_id = "user-test"

    def __init__(self):
        self.approvals: list[dict] = []
        self.expenses: list[dict] = []
        self.invoices: list[dict] = []
        self.docs: list[dict] = []
        self.issues: list[dict] = []
        self.toggl_entries: list[dict] = []

    def request_approval(self, tool, arguments, summary):
        self.approvals.append({"tool": tool, "args": arguments, "summary": summary})
        return {"id": f"a-{len(self.approvals)}", "status": "pending"}

    def create_expense(self, payload):
        self.expenses.append(payload)
        return {**payload, "id": f"e-{len(self.expenses)}"}

    def create_invoice(self, payload):
        self.invoices.append(payload)
        return {**payload, "id": f"INV-{len(self.invoices):04d}"}

    def create_google_doc(self, payload):
        self.docs.append(payload)
        return {"url": f"https://docs.google.com/d{len(self.docs)}", "title": payload.get("project_name", "")}

    def list_linear_teams(self):
        return [{"id": "team-1", "name": "Engineering"}]

    def create_linear_issue(self, payload):
        self.issues.append(payload)
        return {**payload, "id": f"ISS-{len(self.issues)}"}

    def get_toggl_entries(self, start, end):
        return list(self.toggl_entries)

    def close(self):
        pass


# ---------- Expenses: only large amounts gated ----------

class TestExpenseApprovalGate:
    def test_small_expense_passes_through(self):
        node = FakeNode()
        srv = ExpensesMcpServer(node)
        result = srv.call_tool("create_expense", {"vendor": "Coffee", "amount": 5.0})
        assert result.get("isError") is False
        # Real expense persisted, no approval requested.
        assert len(node.expenses) == 1
        assert node.approvals == []

    def test_large_expense_is_gated(self):
        node = FakeNode()
        srv = ExpensesMcpServer(node)
        result = srv.call_tool("create_expense", {"vendor": "Contractor", "amount": 500.0})
        text = result["content"][0]["text"]
        assert "PENDING_APPROVAL" in text
        # Approval enqueued, expense NOT yet persisted.
        assert len(node.approvals) == 1
        assert node.expenses == []

    def test_bypass_executes_real_action(self):
        node = FakeNode()
        srv = ExpensesMcpServer(node)
        srv._approval_bypass = True  # simulates the post-approval re-invocation
        result = srv.call_tool("create_expense", {"vendor": "Contractor", "amount": 500.0})
        assert result.get("isError") is False
        assert len(node.expenses) == 1
        assert node.approvals == []  # bypass skipped the queue


# ---------- Linear issues: ALWAYS gated ----------

class TestLinearIssueGate:
    def test_create_issue_is_gated(self):
        node = FakeNode()
        srv = IssueTrackerMcpServer(node)
        result = srv.call_tool("create_linear_issue", {"title": "Bug", "description": "thing broke"})
        text = result["content"][0]["text"]
        assert "PENDING_APPROVAL" in text
        assert len(node.approvals) == 1
        assert node.issues == []

    def test_bypass_executes(self):
        node = FakeNode()
        srv = IssueTrackerMcpServer(node)
        srv._approval_bypass = True
        srv.call_tool("create_linear_issue", {"title": "Bug", "description": "x"})
        assert len(node.issues) == 1


# ---------- Documents/proposal: ALWAYS gated ----------

class TestProposalGate:
    def test_proposal_is_gated(self):
        node = FakeNode()
        srv = DocumentsMcpServer(node)
        result = srv.call_tool("generate_proposal", {
            "client_name": "Acme",
            "project_name": "Site",
            "summary": "x",
            "scope": ["a"],
            "estimated_budget": 5000,
            "estimated_days": 14,
            "start_date": "2026-05-01",
        })
        text = result["content"][0]["text"]
        assert "PENDING_APPROVAL" in text
        assert "Acme" in text
        assert len(node.approvals) == 1
        assert node.docs == []


# ---------- Auto-billing: real path ----------

class TestAutoBilling:
    def test_create_invoice_from_entries_gated(self):
        node = FakeNode()
        # 2h on "design", 3h on "dev"
        node.toggl_entries = [
            {"description": "design work", "duration": 2 * 3600},
            {"description": "dev work",    "duration": 3 * 3600},
        ]
        srv = TimesheetsMcpServer(node)
        result = srv.call_tool("create_invoice_from_entries", {
            "client": "Acme",
            "start_date": "2026-04-01",
            "end_date": "2026-04-07",
            "hourly_rate": 100,
        })
        text = result["content"][0]["text"]
        assert "PENDING_APPROVAL" in text
        assert len(node.approvals) == 1
        # The summary should include the computed total.
        assert "500" in node.approvals[0]["summary"]
        assert node.invoices == []  # not yet — gated

    def test_create_invoice_bypass_actually_files(self):
        node = FakeNode()
        node.toggl_entries = [{"description": "code review", "duration": 4 * 3600}]
        srv = TimesheetsMcpServer(node)
        srv._approval_bypass = True
        result = srv.call_tool("create_invoice_from_entries", {
            "client": "Beta Corp",
            "start_date": "2026-04-01",
            "end_date": "2026-04-07",
            "hourly_rate": 75,
        })
        assert result.get("isError") is False
        assert len(node.invoices) == 1
        assert node.invoices[0]["amount"] == 4 * 75
        assert node.invoices[0]["client"] == "Beta Corp"

    def test_no_billable_time_short_circuits(self):
        node = FakeNode()
        node.toggl_entries = []  # nothing
        srv = TimesheetsMcpServer(node)
        result = srv.call_tool("create_invoice_from_entries", {
            "client": "Acme",
            "start_date": "2026-04-01",
            "end_date": "2026-04-07",
            "hourly_rate": 100,
        })
        text = result["content"][0]["text"]
        assert "no billable time" in text
        assert node.approvals == []


# ---------- Approval queue down: graceful degradation ----------

class TestApprovalFailure:
    def test_request_approval_failure_blocks_action(self):
        node = FakeNode()

        def boom(*_a, **_kw):
            raise RuntimeError("queue down")
        node.request_approval = boom

        srv = ExpensesMcpServer(node)
        result = srv.call_tool("create_expense", {"vendor": "Big", "amount": 500.0})
        text = result["content"][0]["text"]
        assert "PENDING_APPROVAL_ERROR" in text
        # Action did NOT happen — better to fail closed than silently bill.
        assert node.expenses == []
