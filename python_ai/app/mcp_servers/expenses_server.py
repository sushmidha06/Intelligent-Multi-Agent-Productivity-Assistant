"""Expenses MCP server.

Lets the agent log expenses on the user's behalf — pairs naturally with the
Gmail tools so the user can ask "log my Vercel receipt from yesterday as a
hosting expense for the Northwind project" and the agent will:
  1. gmail__search_emails(query="vercel newer_than:2d")
  2. gmail__get_email_body(uid=...)
  3. firestore__list_projects (to find Northwind's project id)
  4. expenses__create(vendor=..., amount=..., projectId=...)
"""

from __future__ import annotations

from ..node_client import NodeClient
from .base import McpError, McpServer

ALLOWED_CATEGORIES = [
    "Software", "Hardware", "Hosting & infra", "Subcontractor",
    "Travel", "Meals", "Office", "Marketing", "Taxes & fees", "Other",
]


class ExpensesMcpServer(McpServer):
    server_name = "expenses"

    def __init__(self, node: NodeClient):
        self.node = node
        super().__init__()

    def _register_tools(self) -> None:
        self._tool(
            "create_expense",
            (
                "Log a new expense for the user. Use this after the user confirms an expense, "
                "or after extracting one from an email body. If a project is involved, look it "
                "up first with firestore__list_projects and pass its id as `project_id` so the "
                "expense rolls up into that project's spent total."
            ),
            {
                "type": "object",
                "properties": {
                    "vendor":   {"type": "string", "description": "Who charged the user (e.g. 'Vercel')."},
                    "amount":   {"type": "number", "description": "Amount in the user's primary currency. Number only, no symbols."},
                    "date":     {"type": "string", "description": "ISO date YYYY-MM-DD. Defaults to today."},
                    "category": {"type": "string", "enum": ALLOWED_CATEGORIES, "default": "Other"},
                    "project_id": {"type": "string", "description": "Optional Firestore project id to attribute the expense to."},
                    "notes":    {"type": "string", "description": "Optional free-text note."},
                },
                "required": ["vendor", "amount"],
                "additionalProperties": False,
            },
            self._create,
        )

    # Anything at or above this amount goes through the human approval queue.
    # Small lattes don't need an approval gate; a $500 contractor charge does.
    APPROVAL_THRESHOLD = 200.0

    def _create(
        self,
        vendor: str,
        amount: float,
        date: str | None = None,
        category: str = "Other",
        project_id: str | None = None,
        notes: str | None = None,
    ) -> dict | str:
        if not vendor or not vendor.strip():
            raise McpError("vendor is required")
        if amount is None or float(amount) <= 0:
            raise McpError("amount must be a positive number")
        if category not in ALLOWED_CATEGORIES:
            category = "Other"
        amount = float(amount)
        payload = {
            "vendor": vendor.strip(),
            "amount": amount,
            "category": category,
            "projectId": project_id or None,
            "notes": notes or "",
        }
        if date:
            payload["date"] = date

        def do_create():
            try:
                created = self.node.create_expense(payload)
                return {
                    "id": created.get("id"),
                    "vendor": created.get("vendor"),
                    "amount": created.get("amount"),
                    "category": created.get("category"),
                    "projectId": created.get("projectId"),
                    "date": created.get("date"),
                }
            except Exception as e:  # noqa: BLE001
                raise McpError(f"could not create expense: {e}")

        # Gate large expenses through human approval; pass small ones straight through.
        if amount >= self.APPROVAL_THRESHOLD:
            return self._gate_with_approval(
                tool_name="expenses__create_expense",
                args=payload,
                summary=f"log a {category} expense of {amount:,.2f} from {payload['vendor']}",
                do=do_create,
            )
        return do_create()
