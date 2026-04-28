from typing import Any
from .base import McpServer


class IssueTrackerMcpServer(McpServer):
    server_name = "issues"
    server_version = "1.0.0"

    def __init__(self, node: Any):
        self.node = node
        super().__init__()

    def _register_tools(self) -> None:
        self._tool(
            "list_linear_teams",
            "Fetches the list of teams in your Linear workspace. "
            "Use this to find the team ID needed to create an issue.",
            {},
            self._list_teams,
        )
        self._tool(
            "create_linear_issue",
            "Creates a new ticket in Linear. Use this when a client reports a bug or "
            "requests a feature via email.",
            {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Short, descriptive title of the issue"},
                    "description": {"type": "string", "description": "Detailed explanation of the problem or request"},
                    "team_id": {"type": "string", "description": "Optional ID of the team. If omitted, uses the first team found."},
                    "priority": {"type": "integer", "description": "0 (no priority), 1 (urgent), 2 (high), 3 (medium), 4 (low)"},
                },
                "required": ["title", "description"],
                "additionalProperties": False,
            },
            self._create_issue,
        )

    def _list_teams(self) -> list[dict]:
        try:
            return self.node.list_linear_teams()
        except Exception as e:
            return [{"error": str(e)}]

    def _create_issue(self, title: str, description: str, team_id: str | None = None, priority: int = 0) -> dict | str:
        # Linear issues are visible to teammates / clients — gate behind approval
        # so the agent can't silently file tickets on the user's behalf.
        #
        # IMPORTANT: the args dict here is what gets stored in the approval queue
        # and re-spread back into THIS handler on approve. Keys MUST match the
        # handler's parameter names (snake_case `team_id`), NOT the Linear API
        # field name (`teamId`). The camelCase translation happens at the Node
        # boundary inside `do_create`, not here.
        args = {
            "title": title,
            "description": description,
            "team_id": team_id,
            "priority": priority,
        }

        def do_create():
            try:
                return self.node.create_linear_issue({
                    "title": title,
                    "description": description,
                    "teamId": team_id,
                    "priority": priority,
                })
            except Exception as e:  # noqa: BLE001
                return {"error": str(e)}

        return self._gate_with_approval(
            tool_name="issues__create_linear_issue",
            args=args,
            summary=f'create Linear issue "{title[:60]}"',
            do=do_create,
        )
