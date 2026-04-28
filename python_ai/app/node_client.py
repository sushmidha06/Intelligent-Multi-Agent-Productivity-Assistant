from __future__ import annotations

import httpx

from .security import sign_service_token
from .settings import settings


class NodeClient:
    """Talks back to the Node backend for per-user Firestore data and
    decrypted integration secrets. All requests are authed with a short-lived
    HS256 JWT tied to the specific userId."""

    def __init__(self, user_id: str, email: str | None = None):
        self.user_id = user_id
        self.email = email
        self._client = httpx.Client(base_url=settings.NODE_API_BASE_URL, timeout=15.0)

    def _token(self) -> str:
        return sign_service_token(self.user_id, self.email)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token()}"}

    def get_collection(self, name: str) -> list[dict]:
        r = self._client.get(f"/internal/data/{name}", headers=self._headers())
        r.raise_for_status()
        return r.json().get("items", [])

    def get_connection(self, provider: str) -> dict | None:
        r = self._client.get(f"/internal/connections/{provider}", headers=self._headers())
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    def get_integrations(self) -> list[str]:
        r = self._client.get("/internal/integrations", headers=self._headers())
        r.raise_for_status()
        return r.json().get("integrations", [])

    def get_email_bodies(self) -> list[dict]:
        """Pulls indexed email bodies from the per-user knowledge base
        (populated by POST /api/inbox/sync-rag)."""
        r = self._client.get("/internal/email-bodies", headers=self._headers())
        if r.status_code == 404:
            return []
        r.raise_for_status()
        return r.json().get("items", [])

    def create_expense(self, payload: dict) -> dict:
        r = self._client.post("/internal/expenses", headers=self._headers(), json=payload)
        r.raise_for_status()
        return r.json()

    def create_invoice(self, payload: dict) -> dict:
        """Used by the timesheet auto-billing tool to file an invoice on behalf
        of the user. Returns the saved invoice document including its id."""
        r = self._client.post("/internal/billing", headers=self._headers(), json=payload, timeout=15.0)
        r.raise_for_status()
        return r.json()

    def push_notification(self, title: str, body: str, kind: str = "info") -> dict:
        """Pushes an in-app notification for this user via the Node backend.
        Used by proactive agents to surface findings."""
        r = self._client.post(
            "/internal/notifications/push",
            headers=self._headers(),
            json={"title": title, "body": body, "kind": kind},
            timeout=10.0,
        )
        r.raise_for_status()
        return r.json()

    def create_google_doc(self, payload: dict) -> dict:
        """Asks the Node backend to create and format a Google Doc.
        Returns { "url", "documentId", "title" }."""
        r = self._client.post(
            "/internal/documents/google-doc",
            headers=self._headers(),
            json=payload,
            timeout=30.0,
        )
        r.raise_for_status()
        return r.json()

    def get_toggl_entries(self, start: str, end: str) -> list[dict]:
        """Fetches Toggl time entries from the Node backend."""
        r = self._client.get(
            "/internal/timesheets/toggl",
            headers=self._headers(),
            params={"start": start, "end": end},
            timeout=15.0,
        )
        r.raise_for_status()
        return r.json().get("entries", [])

    def list_linear_teams(self) -> list[dict]:
        """Fetches the list of Linear teams."""
        r = self._client.get("/internal/issues/linear/teams", headers=self._headers())
        r.raise_for_status()
        return r.json().get("teams", [])

    def create_linear_issue(self, payload: dict) -> dict:
        """Creates a Linear issue. On non-2xx, surface the Node error body
        instead of the bare HTTP status — otherwise the user just sees
        '500 Internal Server Error' with no clue which Linear-side check failed."""
        r = self._client.post("/internal/issues/linear", headers=self._headers(), json=payload)
        if r.status_code >= 400:
            try:
                detail = r.json().get("error") or r.text
            except Exception:  # noqa: BLE001
                detail = r.text or f"HTTP {r.status_code}"
            raise RuntimeError(f"Linear: {detail}")
        return r.json()

    def request_approval(self, tool: str, arguments: dict, summary: str) -> dict:
        """Sends a sensitive action to the Node backend for human approval."""
        r = self._client.post(
            "/internal/approvals",
            headers=self._headers(),
            json={"tool": tool, "arguments": arguments, "summary": summary},
            timeout=10.0,
        )
        r.raise_for_status()
        return r.json()

    @staticmethod
    def lookup_bot_mapping(platform: str, platform_user_id: str) -> str | None:
        """Resolves a Slack/Discord platformUserId to our internal userId via
        the Node backend. Returns None when the user hasn't linked their
        account. Used by the Slack webhook handler before it knows whose
        Orchestrator to spin up — that's why this is a static method that
        doesn't need a NodeClient instance bound to a userId."""
        # The Node endpoint accepts any valid service token (auth is the
        # shared secret itself); we sign with a sentinel `userId` so the
        # token has the right shape.
        token = sign_service_token("__bot_lookup__")
        with httpx.Client(base_url=settings.NODE_API_BASE_URL, timeout=10.0) as c:
            r = c.get(
                f"/internal/bot-mapping/{platform}/{platform_user_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json().get("internalUserId")

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
