"""Read-only Gmail access for Jarvis (personal OAuth, local token).

First use needs a one-time browser authorization: run `python -m jarvis --auth-gmail`
(or the first email tool call triggers it). The token is cached in
google_token.json (gitignored) and refreshed automatically after that.
Scope is gmail.readonly — Jarvis can read/summarize, never send or delete.
"""

from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
BASE = Path(__file__).resolve().parent.parent
CRED = BASE / "google_credentials.json"
TOKEN = BASE / "google_token.json"


class Gmail:
    def __init__(self):
        self._svc = None

    def available(self) -> bool:
        return CRED.exists()

    def _service(self, interactive=False):
        if self._svc:
            return self._svc
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build

        creds = None
        if TOKEN.exists():
            creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            elif interactive:
                flow = InstalledAppFlow.from_client_secrets_file(str(CRED), SCOPES)
                creds = flow.run_local_server(port=0)  # opens the browser once
            else:
                raise RuntimeError("Gmail not authorized — run: python -m jarvis --auth-gmail")
            TOKEN.write_text(creds.to_json(), encoding="utf-8")
        self._svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
        return self._svc

    def authorize(self) -> str:
        self._service(interactive=True)
        return "Gmail authorized ✓"

    def recent(self, n: int = 8, query: str = "") -> list[dict]:
        svc = self._service()
        listing = svc.users().messages().list(
            userId="me", maxResults=n, q=query or "in:inbox").execute()
        out = []
        for m in listing.get("messages", []):
            msg = svc.users().messages().get(
                userId="me", id=m["id"], format="metadata",
                metadataHeaders=["From", "Subject", "Date"]).execute()
            h = {x["name"]: x["value"] for x in msg.get("payload", {}).get("headers", [])}
            out.append({"from": h.get("From", ""), "subject": h.get("Subject", "(no subject)"),
                        "date": h.get("Date", ""), "snippet": msg.get("snippet", "")})
        return out
