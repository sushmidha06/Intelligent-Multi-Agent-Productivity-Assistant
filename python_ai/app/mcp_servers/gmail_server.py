"""Gmail MCP server. Uses Gmail IMAP/SMTP with an app password (user pastes in
Settings → Integrations → Gmail)."""

from __future__ import annotations

import email
import imaplib
import smtplib
from email.message import EmailMessage
from email.header import decode_header
from email.utils import parsedate_to_datetime
from typing import Any

from ..node_client import NodeClient
from .base import McpError, McpServer

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993


def _decode(value: str | None) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    out = []
    for part, enc in parts:
        if isinstance(part, bytes):
            try:
                out.append(part.decode(enc or "utf-8", errors="replace"))
            except LookupError:
                out.append(part.decode("utf-8", errors="replace"))
        else:
            out.append(part)
    return "".join(out)


def _body_preview(msg: email.message.Message, max_chars: int = 280) -> str:
    """Return a plain-text preview from the message body."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                try:
                    text = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", errors="replace")
                    return text.strip().replace("\r\n", "\n")[:max_chars]
                except Exception:
                    continue
        return ""
    try:
        payload = msg.get_payload(decode=True)
        if payload:
            return payload.decode(msg.get_content_charset() or "utf-8", errors="replace").strip()[:max_chars]
    except Exception:
        return ""
    return ""


class GmailMcpServer(McpServer):
    server_name = "gmail"

    def __init__(self, node: NodeClient):
        self.node = node
        self._creds: dict[str, str] | None = None
        super().__init__()

    # --- auth ---
    def _credentials(self) -> tuple[str, str]:
        if self._creds and self._creds.get("email") and self._creds.get("password"):
            return self._creds["email"], self._creds["password"]
        conn = self.node.get_connection("gmail")
        if not conn:
            raise McpError("Gmail is not connected. Ask the user to connect it in Integrations.")
        metadata = conn.get("metadata") or {}
        secrets = conn.get("secrets") or {}
        email_addr = metadata.get("email") or secrets.get("email")
        password = secrets.get("appPassword") or secrets.get("password")
        if not email_addr or not password:
            raise McpError("Gmail connection is missing credentials.")
        self._creds = {"email": email_addr, "password": password}
        return email_addr, password

    def _imap(self):
        email_addr, password = self._credentials()
        conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        try:
            conn.login(email_addr, password)
        except imaplib.IMAP4.error as e:
            raise McpError(f"IMAP login failed: {e}. Check the app password is correct.")
        return conn

    # --- tools ---
    def _register_tools(self) -> None:
        self._tool(
            "list_recent_emails",
            "List the most recent emails in the user's Gmail inbox.",
            {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                    "unread_only": {"type": "boolean", "default": False},
                },
                "additionalProperties": False,
            },
            self._list_recent,
        )
        self._tool(
            "search_emails",
            "Search the user's inbox by query (Gmail search syntax). E.g. 'from:client subject:invoice'.",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Gmail search string, e.g. 'from:acme newer_than:7d'"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 30, "default": 10},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            self._search,
        )
        self._tool(
            "get_email_body",
            "Fetch the full text body of a specific email by its UID.",
            {
                "type": "object",
                "properties": {"uid": {"type": "string"}},
                "required": ["uid"],
                "additionalProperties": False,
            },
            self._get_body,
        )
        self._tool(
            "send_email",
            "Send a new email from the user's Gmail account.",
            {
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "Recipient email address"},
                    "subject": {"type": "string", "description": "Email subject line"},
                    "body": {"type": "string", "description": "Full plain-text body of the email"},
                },
                "required": ["to", "subject", "body"],
                "additionalProperties": False,
            },
            self._send_email,
        )

    # --- handlers ---
    def _list_recent(self, limit: int = 10, unread_only: bool = False) -> dict:
        imap = self._imap()
        try:
            imap.select("INBOX", readonly=True)
            criteria = "UNSEEN" if unread_only else "ALL"
            status, data = imap.uid("search", None, criteria)
            if status != "OK":
                raise McpError(f"IMAP search failed: {status}")
            uids = (data[0] or b"").split()[-limit:][::-1]
            return {"count": len(uids), "emails": [self._summarise(imap, u) for u in uids]}
        finally:
            imap.logout()

    def _search(self, query: str, limit: int = 10) -> dict:
        # Gmail supports its search syntax via the X-GM-RAW extension.
        imap = self._imap()
        try:
            imap.select("INBOX", readonly=True)
            status, data = imap.uid("search", None, "X-GM-RAW", f'"{query}"')
            if status != "OK":
                raise McpError(f"IMAP search failed: {status}")
            uids = (data[0] or b"").split()[-limit:][::-1]
            return {"query": query, "count": len(uids), "emails": [self._summarise(imap, u) for u in uids]}
        finally:
            imap.logout()

    def _get_body(self, uid: str) -> dict:
        imap = self._imap()
        try:
            imap.select("INBOX", readonly=True)
            status, data = imap.uid("fetch", uid, "(RFC822)")
            if status != "OK" or not data or not data[0]:
                raise McpError(f"could not fetch uid {uid}")
            raw = data[0][1]
            msg = email.message_from_bytes(raw)
            return {
                "uid": uid,
                "subject": _decode(msg.get("Subject")),
                "from": _decode(msg.get("From")),
                "date": _format_date(msg.get("Date")),
                "body": _body_preview(msg, max_chars=4000),
            }
        finally:
            imap.logout()

    # --- helpers ---
    def _summarise(self, imap: imaplib.IMAP4_SSL, uid: bytes) -> dict[str, Any]:
        status, data = imap.uid("fetch", uid, "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE)])")
        if status != "OK":
            return {"uid": uid.decode(), "error": "fetch failed"}
        headers = email.message_from_bytes(data[0][1]) if data and data[0] else None
        status2, data2 = imap.uid("fetch", uid, "(BODY.PEEK[TEXT])")
        preview = ""
        if status2 == "OK" and data2 and data2[0]:
            try:
                preview = data2[0][1].decode(errors="replace")[:200].replace("\r\n", " ").strip()
            except Exception:
                preview = ""
        return {
            "uid": uid.decode(),
            "subject": _decode(headers.get("Subject")) if headers else "",
            "from": _decode(headers.get("From")) if headers else "",
            "date": _format_date(headers.get("Date")) if headers else "",
            "preview": preview,
        }

    def _send_email(self, to: str, subject: str, body: str) -> dict:
        email_addr, password = self._credentials()
        msg = EmailMessage()
        msg.set_content(body)
        msg["Subject"] = subject
        msg["From"] = email_addr
        msg["To"] = to

        def do_send():
            try:
                with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
                    smtp.login(email_addr, password)
                    smtp.send_message(msg)
                return {"success": True, "to": to, "subject": subject}
            except Exception as e:
                return {"error": str(e)}

        return self._gate_with_approval(
            tool_name="gmail__send_email",
            args={"to": to, "subject": subject, "body": body},
            summary=f"send email to {to}: {subject}",
            do=do_send,
        )


def _format_date(raw: str | None) -> str:
    if not raw:
        return ""
    try:
        return parsedate_to_datetime(raw).isoformat()
    except Exception:
        return raw or ""
