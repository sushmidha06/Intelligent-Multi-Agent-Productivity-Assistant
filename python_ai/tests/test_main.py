"""Integration tests for the FastAPI app — endpoints, auth, guardrails wiring.

Heavier external pieces (Gemini, Node API) are mocked at the Orchestrator
boundary so tests run offline."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.security import sign_service_token


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def auth_header():
    tok = sign_service_token("user-test", "u@example.com")
    return {"Authorization": f"Bearer {tok}"}


# ---------- Public endpoints ----------

def test_root_lists_endpoints(client):
    r = client.get("/")
    assert r.status_code == 200
    body = r.json()
    assert body["service"] == "sushmi-mcp-ai"
    assert "endpoints" in body


def test_health_returns_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_metrics_endpoint_serves_prometheus(client):
    # Hit /health a couple times to populate counters.
    client.get("/health")
    client.get("/health")
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "http_requests_total" in r.text


def test_request_id_echoed_in_header(client):
    r = client.get("/health", headers={"X-Request-Id": "test-rid-42"})
    assert r.headers.get("x-request-id") == "test-rid-42"


def test_request_id_minted_when_absent(client):
    r = client.get("/health")
    assert r.headers.get("x-request-id")  # non-empty


# ---------- Auth ----------

def test_chat_requires_auth(client):
    r = client.post("/chat", json={"message": "hi"})
    assert r.status_code == 401


def test_mcp_servers_requires_auth(client):
    r = client.get("/mcp/servers")
    assert r.status_code == 401


# ---------- Chat guardrails ----------

def test_chat_rejects_empty_message(client, auth_header):
    r = client.post("/chat", json={"message": "   "}, headers=auth_header)
    assert r.status_code == 400


def test_chat_rejects_oversize(client, auth_header):
    r = client.post("/chat", json={"message": "x" * 10000}, headers=auth_header)
    assert r.status_code == 400


# ---------- Chat happy path (mocked Orchestrator) ----------

class _FakeOrchestrator:
    def __init__(self, *_, **__):
        self.servers = []

    def run(self, message, history=None):
        return {
            "response": "Here you go: contact me at jane@example.com",
            "tool_calls": [{"tool": "firestore__list_projects", "input": {}, "output": "[]"}],
            "tools_available": ["firestore__list_projects"],
            "plan": "1. Call firestore__list_projects\n2. Reply",
        }

    def close(self):
        pass


def test_chat_happy_path_redacts_pii(client, auth_header):
    with patch("app.main.Orchestrator", _FakeOrchestrator):
        r = client.post("/chat", json={"message": "list my projects please"}, headers=auth_header)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "[redacted-email]" in body["response"]
    assert "jane@example.com" not in body["response"]
    assert body["pii_redactions"] == 1
    assert body["plan"]
    assert body["tool_calls"][0]["tool"] == "firestore__list_projects"


def test_chat_rate_limit(client, auth_header):
    """Burst past the limit -> 429."""
    from app.guardrails import RATE_LIMIT_PER_HOUR

    with patch("app.main.Orchestrator", _FakeOrchestrator):
        # The conftest may have already used some quota for this user across tests;
        # use a dedicated token to start fresh.
        from app.security import sign_service_token as _sign
        tok = _sign("burst-user", "b@x.com")
        h = {"Authorization": f"Bearer {tok}"}
        last_status = 200
        for _ in range(RATE_LIMIT_PER_HOUR + 5):
            last_status = client.post("/chat", json={"message": "hi there"}, headers=h).status_code
            if last_status == 429:
                break
        assert last_status == 429
