from typing import Any
from .base import McpServer


class TimesheetsMcpServer(McpServer):
    server_name = "timesheets"
    server_version = "1.1.0"

    def __init__(self, node: Any):
        self.node = node
        super().__init__()

    def _register_tools(self) -> None:
        self._tool(
            "list_time_entries",
            "Fetches time entries from Toggl Track for a specific date range. "
            "Use this to see how much time was spent on specific tasks or projects.",
            {
                "type": "object",
                "properties": {
                    "start_date": {"type": "string", "description": "ISO-8601 start date (YYYY-MM-DD)"},
                    "end_date": {"type": "string", "description": "ISO-8601 end date (YYYY-MM-DD)"},
                },
                "required": ["start_date", "end_date"],
                "additionalProperties": False,
            },
            self._list_time_entries,
        )
        self._tool(
            "create_invoice_from_entries",
            (
                "Auto-bills a client by pulling Toggl entries for a date range, "
                "summing them at the given hourly rate, and filing an invoice. "
                "This is the auto-billing path: timesheet → invoice in one call. "
                "Always gated behind human approval since it creates a billable document."
            ),
            {
                "type": "object",
                "properties": {
                    "client": {"type": "string", "description": "Client name to bill"},
                    "start_date": {"type": "string", "description": "ISO-8601 start date (YYYY-MM-DD)"},
                    "end_date": {"type": "string", "description": "ISO-8601 end date (YYYY-MM-DD)"},
                    "hourly_rate": {"type": "number", "description": "Rate in the user's primary currency"},
                    "due_date": {"type": "string", "description": "Optional ISO date (defaults to issued + 30 days)"},
                },
                "required": ["client", "start_date", "end_date", "hourly_rate"],
                "additionalProperties": False,
            },
            self._create_invoice_from_entries,
        )

    def _list_time_entries(self, start_date: str, end_date: str) -> dict:
        try:
            entries = self.node.get_toggl_entries(start_date, end_date)
            
            # Group by description for a better summary
            summary = {}
            total_seconds = 0
            for e in entries:
                desc = e.get("description") or "(no description)"
                dur = e.get("duration", 0)
                if dur < 0: continue # ongoing timer
                summary[desc] = summary.get(desc, 0) + dur
                total_seconds += dur

            formatted_entries = []
            for desc, seconds in summary.items():
                hours = seconds / 3600
                formatted_entries.append({
                    "task": desc,
                    "hours": round(hours, 2),
                    "seconds": seconds
                })

            return {
                "period": f"{start_date} to {end_date}",
                "total_hours": round(total_seconds / 3600, 2),
                "tasks": formatted_entries,
                "raw_entries_count": len(entries)
            }
        except Exception as e:
            return {"error": str(e)}

    def _create_invoice_from_entries(
        self,
        client: str,
        start_date: str,
        end_date: str,
        hourly_rate: float,
        due_date: str | None = None,
    ):
        """Pull Toggl entries → sum hours → create an invoice. Each step is
        small enough that the agent can do it manually, but bundling it as one
        tool is the actual 'auto-billing' the system advertises."""
        # Step 1: get hours.
        entries = self.node.get_toggl_entries(start_date, end_date) or []
        total_seconds = 0
        line_items: list[dict] = []
        for e in entries:
            dur = int(e.get("duration", 0))
            if dur <= 0:
                continue
            total_seconds += dur
            line_items.append({
                "task": e.get("description") or "(no description)",
                "hours": round(dur / 3600, 2),
            })
        total_hours = round(total_seconds / 3600, 2)
        if total_hours <= 0:
            return {"error": f"no billable time found between {start_date} and {end_date}"}

        amount = round(total_hours * float(hourly_rate), 2)
        payload = {
            "client": client,
            "amount": amount,
            "dueDate": due_date,
            "lineItems": line_items[:50],  # cap so we don't blow Firestore doc-size limits
        }
        summary = (
            f"create invoice for {client}: {total_hours}h × ${hourly_rate}/h = "
            f"${amount:,.2f} (period {start_date} → {end_date})"
        )

        def do_create():
            try:
                saved = self.node.create_invoice(payload)
                return {
                    "ok": True,
                    "invoice_id": saved.get("id"),
                    "client": saved.get("client"),
                    "amount": saved.get("amount"),
                    "hours_billed": total_hours,
                    "rate": hourly_rate,
                    "period": f"{start_date} to {end_date}",
                    "task_count": len(line_items),
                }
            except Exception as e:  # noqa: BLE001
                return {"error": f"could not create invoice: {e}"}

        return self._gate_with_approval(
            tool_name="timesheets__create_invoice_from_entries",
            args=payload,
            summary=summary,
            do=do_create,
        )
