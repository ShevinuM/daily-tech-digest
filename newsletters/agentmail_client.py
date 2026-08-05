"""AgentMail REST client — plain urllib, no SDK.

Base URL and auth confirmed against https://docs.agentmail.to/api-reference:
Bearer token in `Authorization`, base `https://api.agentmail.to/v0`. This
replaces the old interactive MCP connector, which needed one-time OAuth
approval that can't happen in unattended CI.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

API_BASE = "https://api.agentmail.to/v0"
TIMEOUT = 30


def _request(method: str, path: str, *, api_key: str, params: dict | None = None,
             timeout: int = TIMEOUT) -> dict:
    url = f"{API_BASE}{path}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params, doseq=True)}"
    req = urllib.request.Request(url, method=method, headers={
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        raise RuntimeError(f"AgentMail {method} {path} -> HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"AgentMail {method} {path} -> {e.reason}") from e


def list_messages(inbox_id: str, api_key: str, *, after: str | None = None,
                   limit: int = 100, from_sender: str | None = None) -> list[dict]:
    """All message metadata (no body) newer than `after` (ISO 8601), paginated.
    `from_sender` filters to messages whose sender contains that substring."""
    out: list[dict] = []
    page_token = None
    while True:
        params = {"limit": limit}
        if after:
            params["after"] = after
        if from_sender:
            params["from"] = [from_sender]
        if page_token:
            params["page_token"] = page_token
        data = _request("GET", f"/inboxes/{inbox_id}/messages", api_key=api_key, params=params)
        out.extend(data.get("messages", []))
        page_token = data.get("next_page_token")
        if not page_token:
            break
    return out


def get_thread(inbox_id: str, thread_id: str, api_key: str) -> dict:
    """Full thread, including each message's text/html/extracted_text/extracted_html."""
    return _request("GET", f"/inboxes/{inbox_id}/threads/{thread_id}", api_key=api_key)


def delete_thread(inbox_id: str, thread_id: str, api_key: str) -> None:
    _request("DELETE", f"/inboxes/{inbox_id}/threads/{thread_id}", api_key=api_key)
