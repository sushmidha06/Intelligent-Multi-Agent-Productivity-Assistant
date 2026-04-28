"""Firestore MCP server — exposes the user's own CRUD data
(projects, invoices, emails, alerts) to the agent. All calls are scoped
by the user bound at construction time."""

from __future__ import annotations

from ..node_client import NodeClient
from .base import McpError, McpServer


class FirestoreMcpServer(McpServer):
    server_name = "firestore"

    def __init__(self, node: NodeClient):
        self.node = node
        super().__init__()

    def _register_tools(self) -> None:
        self._tool(
            "list_projects",
            "List the current user's projects with their health, client, budget, and deadline.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            self._list_projects,
        )
        self._tool(
            "list_invoices",
            "List the current user's invoices with status (Paid/Pending/Overdue) and amounts.",
            {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["Paid", "Pending", "Overdue", "All"],
                        "description": "Filter by status. Default: All.",
                    }
                },
                "additionalProperties": False,
            },
            self._list_invoices,
        )
        self._tool(
            "get_dashboard_summary",
            "Summarise the user's workspace: revenue, active projects, pending invoices, commit velocity.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            self._dashboard,
        )
        self._tool(
            "list_alerts",
            "List alerts currently active for the user.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            self._alerts,
        )
        self._tool(
            "list_integrations",
            "Check which external accounts (GitHub, Gmail, Razorpay, etc.) are currently connected.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            self._list_integrations,
        )

    # --- handlers ---
    def _list_projects(self) -> dict:
        items = self.node.get_collection("projects")
        return {"count": len(items), "projects": items}

    def _list_integrations(self) -> dict:
        integrations = self.node.get_integrations()
        return {"count": len(integrations), "integrations": integrations}

    def _list_invoices(self, status: str = "All") -> dict:
        items = self.node.get_collection("invoices")
        if status and status != "All":
            items = [i for i in items if i.get("status") == status]
        return {"count": len(items), "invoices": items}

    def _alerts(self) -> dict:
        items = self.node.get_collection("alerts")
        return {"count": len(items), "alerts": items}

    def _dashboard(self) -> dict:
        projects = self.node.get_collection("projects") or []
        invoices = self.node.get_collection("invoices") or []
        revenue = sum((i.get("amount") or 0) for i in invoices if i.get("status") == "Paid")
        pending = sum(1 for i in invoices if i.get("status") != "Paid")
        commits = sum((p.get("commits") or 0) for p in projects)
        if not projects and not invoices:
            raise McpError(
                "User has no projects or invoices yet. Suggest they create one via the Projects or Billing pages."
            )
        return {
            "revenue_paid": revenue,
            "active_projects": len(projects),
            "pending_invoices": pending,
            "total_commits": commits,
        }
