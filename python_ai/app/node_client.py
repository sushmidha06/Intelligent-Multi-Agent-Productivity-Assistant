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

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
