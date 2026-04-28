"""Razorpay MCP server (read-only). The user pastes their key_id + key_secret
in Integrations. Agent can list invoices, payments, and customers."""

from __future__ import annotations

import base64

import httpx

from ..node_client import NodeClient
from .base import McpError, McpServer

RP_BASE = "https://api.razorpay.com/v1"


class RazorpayMcpServer(McpServer):
    server_name = "razorpay"

    def __init__(self, node: NodeClient):
        self.node = node
        self._auth_header: str | None = None
        super().__init__()

    def _auth(self) -> str:
        if self._auth_header:
            return self._auth_header
        conn = self.node.get_connection("razorpay")
        if not conn:
            raise McpError("Razorpay is not connected. Ask the user to connect it in Integrations.")
        secrets = conn.get("secrets") or {}
        key_id = secrets.get("keyId")
        key_secret = secrets.get("keySecret")
        if not key_id or not key_secret:
            raise McpError("Razorpay connection is missing keyId or keySecret.")
        b64 = base64.b64encode(f"{key_id}:{key_secret}".encode()).decode()
        self._auth_header = f"Basic {b64}"
        return self._auth_header

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=RP_BASE,
            headers={"Authorization": self._auth(), "Content-Type": "application/json"},
            timeout=20.0,
        )

    def _register_tools(self) -> None:
        self._tool(
            "list_invoices",
            "List invoices from the Razorpay account.",
            {
                "type": "object",
                "properties": {
                    "count": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
                    "status": {
                        "type": "string",
                        "enum": ["draft", "issued", "partially_paid", "paid", "cancelled", "expired", "deleted"],
                        "description": "Optional: filter by Razorpay status.",
                    },
                },
                "additionalProperties": False,
            },
            self._list_invoices,
        )
        self._tool(
            "list_payments",
            "List recent payments captured in the Razorpay account.",
            {
                "type": "object",
                "properties": {
                    "count": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
                },
                "additionalProperties": False,
            },
            self._list_payments,
        )
        self._tool(
            "list_customers",
            "List customers stored in the Razorpay account.",
            {
                "type": "object",
                "properties": {
                    "count": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
                },
                "additionalProperties": False,
            },
            self._list_customers,
        )
        self._tool(
            "create_invoice",
            "Creates a new draft invoice in Razorpay. This is a SENSITIVE action that requires your approval.",
            {
                "type": "object",
                "properties": {
                    "customer_id": {"type": "string", "description": "The Razorpay customer ID (e.g. cust_...)"},
                    "amount": {"type": "number", "description": "Total amount in major currency units (e.g. 500.00)"},
                    "description": {"type": "string", "description": "Short summary of the items/services"},
                },
                "required": ["customer_id", "amount", "description"],
                "additionalProperties": False,
            },
            self._create_invoice,
        )

    # --- handlers ---
    def _create_invoice(self, customer_id: str, amount: float, description: str) -> str:
        """This handler intercepts the request and sends it for approval."""
        # Check if we are in 'Execution Mode' (called from /approvals/execute)
        # In a real app, we'd use a context variable. 
        # For now, we'll check if a special bypass header is present or if we should just trigger approval.
        
        # LOGIC: If this is the FIRST time the agent calls this, we trigger approval.
        # If the user has already approved it, the /approvals/execute endpoint calls the handler directly.
        
        # To avoid infinite loops, we check a flag.
        import contextvars
        is_bypass = getattr(self, "_approval_bypass", False)
        
        if not is_bypass:
            summary = f"create a Razorpay invoice for ${amount:,.2f} to customer {customer_id}"
            self.node.request_approval("razorpay__create_invoice", {
                "customer_id": customer_id,
                "amount": amount,
                "description": description
            }, summary)
            return f"PENDING_APPROVAL: I've sent a request to create this invoice for ${amount:,.2f}. Please check your Approvals tab to authorize it."

        # ACTUAL EXECUTION (only if bypass is active)
        payload = {
            "type": "invoice",
            "customer_id": customer_id,
            "line_items": [{
                "name": description,
                "amount": int(amount * 100), # Razorpay uses paise/cents
                "currency": "INR" # Or get from preferences
            }]
        }
        with self._client() as c:
            r = c.post("/invoices", json=payload)
            r.raise_for_status()
            data = r.json()
            return f"Success! Invoice created: {data.get('short_url')}"

    def _list_invoices(self, count: int = 10, status: str | None = None) -> dict:
        params: dict = {"count": count}
        if status:
            params["status"] = status
        with self._client() as c:
            r = c.get("/invoices", params=params)
            if r.status_code == 401:
                raise McpError("Razorpay authentication failed. Check the keys are correct (test vs live).")
            r.raise_for_status()
            data = r.json()
        items = [
            {
                "id": it.get("id"),
                "status": it.get("status"),
                "amount": (it.get("amount") or 0) / 100,
                "currency": it.get("currency"),
                "customer_name": (it.get("customer_details") or {}).get("name"),
                "due_by": it.get("expire_by"),
                "short_url": it.get("short_url"),
            }
            for it in (data.get("items") or [])
        ]
        return {"count": len(items), "invoices": items}

    def _list_payments(self, count: int = 10) -> dict:
        with self._client() as c:
            r = c.get("/payments", params={"count": count})
            if r.status_code == 401:
                raise McpError("Razorpay authentication failed.")
            r.raise_for_status()
            data = r.json()
        items = [
            {
                "id": it.get("id"),
                "status": it.get("status"),
                "amount": (it.get("amount") or 0) / 100,
                "currency": it.get("currency"),
                "method": it.get("method"),
                "email": it.get("email"),
                "created_at": it.get("created_at"),
            }
            for it in (data.get("items") or [])
        ]
        return {"count": len(items), "payments": items}

    def _list_customers(self, count: int = 10) -> dict:
        with self._client() as c:
            r = c.get("/customers", params={"count": count})
            if r.status_code == 401:
                raise McpError("Razorpay authentication failed.")
            r.raise_for_status()
            data = r.json()
        items = [
            {"id": it.get("id"), "name": it.get("name"), "email": it.get("email"), "contact": it.get("contact")}
            for it in (data.get("items") or [])
        ]
        return {"count": len(items), "customers": items}
