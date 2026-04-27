"""Smoke test for MCP server contract — every server lists tools and the
schemas validate. We pass a fake NodeClient since these tests don't need
to talk to the real backend.
"""

from app.mcp_servers.expenses_server import ExpensesMcpServer
from app.mcp_servers.firestore_server import FirestoreMcpServer
from app.mcp_servers.gmail_server import GmailMcpServer
from app.mcp_servers.github_server import GithubMcpServer
from app.mcp_servers.calendar_server import CalendarMcpServer
from app.mcp_servers.razorpay_server import RazorpayMcpServer


class FakeNodeClient:
    user_id = "user-test"

    def get_collection(self, _name):
        return []

    def get_email_bodies(self):
        return []

    def post(self, *_a, **_kw):
        return {}

    def get(self, *_a, **_kw):
        return {}

    def close(self):
        pass


SERVERS = [
    FirestoreMcpServer,
    GithubMcpServer,
    GmailMcpServer,
    CalendarMcpServer,
    RazorpayMcpServer,
    ExpensesMcpServer,
]


def test_every_server_exposes_metadata():
    node = FakeNodeClient()
    for cls in SERVERS:
        srv = cls(node)
        assert srv.server_name
        assert srv.server_version
        tools = srv.list_tools()
        assert isinstance(tools, list)
        assert len(tools) >= 1, f"{cls.__name__} exposes no tools"
        for t in tools:
            assert "name" in t
            assert "description" in t
            assert "inputSchema" in t


def test_tool_names_are_unique_per_server():
    node = FakeNodeClient()
    for cls in SERVERS:
        srv = cls(node)
        names = [t["name"] for t in srv.list_tools()]
        assert len(names) == len(set(names)), f"{cls.__name__} has duplicate tool names"


def test_call_unknown_tool_returns_error():
    node = FakeNodeClient()
    srv = FirestoreMcpServer(node)
    result = srv.call_tool("does_not_exist", {})
    # Per MCP spec our servers return TextContent with isError=True on bad calls.
    assert result.get("isError") is True or "error" in str(result).lower()
