"""Gmail MCP server (Gmail API, OAuth).

One-time setup:
  1. Google Cloud: enable the Gmail API, create an OAuth client of type "Desktop app",
     save its JSON as voice-hack/client_secret.json.
  2. python mail.py login      # a Google sign-in window opens; approve access
The OAuth token is kept in Windows Credential Manager (service "voice-agent-gmail"),
never in a file or a prompt. Sending tools are marked destructive, so the voice agent
asks the user out loud before using them.
"""
import base64
import json
import os
import re
import sys
from email.message import EmailMessage

import keyring
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, os.path.join(ROOT, "agent"))
import env  # noqa: E402,F401  (loads voice-hack/.env)

ADDRESS = os.environ.get("MAIL_ADDRESS", "")
CLIENT_SECRET = os.path.join(ROOT, "client_secret.json")
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/gmail.send"]
VAULT = "voice-agent-gmail"

mcp = MCPServer("mail")
READ = ToolAnnotations(readOnlyHint=True)
SEND = ToolAnnotations(readOnlyHint=False, destructiveHint=True)
_service = None


def _creds() -> Credentials:
    raw = keyring.get_password(VAULT, ADDRESS or "default")
    if not raw:
        raise ToolError("Gmail is not connected yet: run `python mail.py login` once.")
    creds = Credentials.from_authorized_user_info(json.loads(raw), SCOPES)
    if not creds.valid and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        keyring.set_password(VAULT, ADDRESS or "default", creds.to_json())
    return creds


def gmail():
    global _service
    if _service is None:
        _service = build("gmail", "v1", credentials=_creds(), cache_discovery=False)
    return _service


def _header(msg: dict, name: str) -> str:
    for h in msg.get("payload", {}).get("headers", []):
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def _text(part: dict) -> str:
    """First text/plain body in a message (falls back to stripped text/html)."""
    mime = part.get("mimeType", "")
    data = part.get("body", {}).get("data")
    if data and mime in ("text/plain", "text/html"):
        text = base64.urlsafe_b64decode(data).decode("utf-8", "replace")
        return re.sub(r"<[^>]+>", " ", text) if mime == "text/html" else text
    subs = part.get("parts", []) or []
    for sub in sorted(subs, key=lambda p: p.get("mimeType") != "text/plain"):
        t = _text(sub)
        if t:
            return t
    return ""


def _clean(text: str) -> str:
    # only the new message: drop quoted history
    lines = [ln for ln in text.splitlines() if not ln.startswith(">")]
    text = re.split(r"\nOn .{5,120} wrote:\s*\n", "\n".join(lines))[0]
    return re.sub(r"\n{3,}", "\n\n", text).strip()


@mcp.tool(annotations=READ)
def list_recent(limit: int = 5, unread_only: bool = False) -> str:
    """List the newest emails in the inbox: id, sender, subject, date."""
    q = "in:inbox" + (" is:unread" if unread_only else "")
    ids = gmail().users().messages().list(userId="me", q=q, maxResults=limit).execute().get("messages", [])
    rows = []
    for m in ids:
        msg = gmail().users().messages().get(userId="me", id=m["id"], format="metadata",
                                             metadataHeaders=["From", "Subject", "Date"]).execute()
        unread = "UNREAD" in msg.get("labelIds", [])
        rows.append(f"id {m['id']} | from {_header(msg, 'From')} | subject {_header(msg, 'Subject')} | "
                    f"{_header(msg, 'Date')}{' | unread' if unread else ''}")
    return "\n".join(rows) or "The inbox is empty."


@mcp.tool(annotations=READ)
def read_email(email_id: str) -> str:
    """Read one email by id: sender, subject and the text of the message."""
    msg = gmail().users().messages().get(userId="me", id=email_id, format="full").execute()
    body = _clean(_text(msg["payload"]))[:2000]
    return (f"From: {_header(msg, 'From')}\nSubject: {_header(msg, 'Subject')}\n"
            f"Date: {_header(msg, 'Date')}\n\n{body}")


def _send(mime: EmailMessage, thread_id: str | None = None) -> dict:
    body = {"raw": base64.urlsafe_b64encode(mime.as_bytes()).decode()}
    if thread_id:
        body["threadId"] = thread_id
    return gmail().users().messages().send(userId="me", body=body).execute()


@mcp.tool(annotations=SEND)
def reply(email_id: str, text: str) -> str:
    """Reply to an email by id with the given text. Sends the email."""
    orig = gmail().users().messages().get(userId="me", id=email_id, format="metadata",
                                          metadataHeaders=["From", "Reply-To", "Subject", "Message-ID"]).execute()
    mime = EmailMessage()
    mime["To"] = _header(orig, "Reply-To") or _header(orig, "From")
    subj = _header(orig, "Subject")
    mime["Subject"] = subj if subj.lower().startswith("re:") else f"Re: {subj}"
    if _header(orig, "Message-ID"):
        mime["In-Reply-To"] = mime["References"] = _header(orig, "Message-ID")
    mime.set_content(text)
    _send(mime, orig.get("threadId"))
    return f"Sent reply to {mime['To']}."


@mcp.tool(annotations=SEND)
def send_email(to: str, subject: str, text: str) -> str:
    """Send a new email. `to` must be an email address (name@example.com), not a name."""
    if "@" not in to:
        return f"ERROR: '{to}' is not an email address. Ask the user for the address; for a messenger, use the screen."
    mime = EmailMessage()
    mime["To"], mime["Subject"] = to, subject
    mime.set_content(text)
    _send(mime)
    return f"Sent email to {to}."


def login():
    from google_auth_oauthlib.flow import InstalledAppFlow
    if not os.path.exists(CLIENT_SECRET):
        raise SystemExit(f"missing {CLIENT_SECRET}: download the OAuth client JSON from Google Cloud")
    creds = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET, SCOPES).run_local_server(
        port=0, login_hint=ADDRESS or None, prompt="consent")
    keyring.set_password(VAULT, ADDRESS or "default", creds.to_json())
    print("Gmail connected; token stored in Windows Credential Manager.")


if __name__ == "__main__":
    if sys.argv[1:] == ["login"]:
        login()
    else:
        mcp.run("stdio")
