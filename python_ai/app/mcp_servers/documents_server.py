import time
from typing import Any
from .base import McpServer


class DocumentsMcpServer(McpServer):
    server_name = "documents"
    server_version = "1.0.0"

    def __init__(self, node: Any):
        self.node = node
        super().__init__()

    def _register_tools(self) -> None:
        self._tool(
            "generate_proposal",
            "Creates a professional project proposal directly in the user's Google Docs. "
            "Analyzes client needs, recommends a budget/timeline based on past work, "
            "and checks calendar availability. Returns the URL of the new Google Doc.",
            {
                "type": "object",
                "properties": {
                    "client_name": {"type": "string", "description": "The client's company or individual name"},
                    "project_name": {"type": "string", "description": "A descriptive title for the project"},
                    "summary": {"type": "string", "description": "High-level summary of the client's request"},
                    "scope": {"type": "array", "items": {"type": "string"}, "description": "List of specific deliverables or phases"},
                    "estimated_budget": {"type": "number", "description": "The proposed total cost"},
                    "estimated_days": {"type": "integer", "description": "Total duration in business days"},
                    "start_date": {"type": "string", "description": "Suggested start date (YYYY-MM-DD)"},
                },
                "required": ["client_name", "project_name", "summary", "scope", "estimated_budget", "estimated_days", "start_date"],
                "additionalProperties": False,
            },
            self._generate_proposal,
        )

    def _generate_proposal(self, **p: Any) -> str:
        # Proposal goes to a client; gate on human approval first.
        def do_generate():
            try:
                result = self.node.create_google_doc(p)
                url = result.get("url", "")
                return (
                    f"Successfully created Google Doc proposal: '{result.get('title')}'\n"
                    f"Edit it here: {url}\n\n"
                    f"Quick Summary:\n"
                    f"- Budget: ${p['estimated_budget']:,}\n"
                    f"- Timeline: {p['estimated_days']} days starting {p['start_date']}"
                )
            except Exception as e:  # noqa: BLE001
                return f"Failed to create Google Doc: {str(e)}"

        summary = (
            f"generate proposal for {p.get('client_name', 'client')} — "
            f"${p.get('estimated_budget', 0):,} over {p.get('estimated_days', 0)} days"
        )
        return self._gate_with_approval(
            tool_name="documents__generate_proposal",
            args=p,
            summary=summary,
            do=do_generate,
        )
