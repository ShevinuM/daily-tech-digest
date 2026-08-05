"""One-click (RFC 8058) unsubscribe handling.

AgentMail's message list/get endpoints expose no parsed List-Unsubscribe
header for the senders we've seen in practice, so the one-click URL — when a
sender includes one at all — has to be found by pattern-matching the raw
message body. Always search the raw `text`/`html` fields, not
`extracted_text`/`extracted_html`, which are geared at reply content and can
be truncated before footer/unsubscribe links.
"""
from __future__ import annotations

import ipaddress
import re
import socket
import urllib.error
import urllib.parse
import urllib.request

_HREF_RE = re.compile(r'href=["\'](https?://[^"\']+)["\']', re.IGNORECASE)
_UNSUB_HINTS = ("unsubscribe", "opt-out", "optout")

# Known ESP click-tracker / redirect domains: fetching these does not
# reliably perform a one-click unsubscribe (some require a follow-up
# confirmation click), so they're treated as "not one-click" and left for
# the reader to handle manually.
_TRACKER_HINTS = ("click.", "clicktrack", "trk.", "/track/", "/CL0/")


def find_unsubscribe_url(text: str, html: str) -> str | None:
    """First plausible unsubscribe URL in the raw body, or None."""
    if html:
        for url in _HREF_RE.findall(html):
            if any(h in url.lower() for h in _UNSUB_HINTS):
                return url.rstrip(").,>")
    if text:
        for raw in re.findall(r"https?://\S+", text):
            url = raw.rstrip(").,>")
            if any(h in url.lower() for h in _UNSUB_HINTS):
                return url
    return None


def is_one_click(url: str) -> bool:
    """Heuristic only — we can't fully verify RFC 8058 compliance without a
    List-Unsubscribe-Post header we don't have access to. Known
    click-tracker/redirect domains are excluded; everything else is assumed
    fetchable."""
    lowered = url.lower()
    return not any(hint in lowered for hint in _TRACKER_HINTS)


def _is_safe_target(url: str) -> bool:
    """Defense-in-depth against SSRF: this URL comes from unsolicited email
    content, so require https and refuse a hostname that resolves to a
    private/loopback/link-local/reserved address (e.g. a cloud metadata
    endpoint). Doesn't close a DNS-rebinding race, but rules out the obvious
    cases for a link we didn't choose."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    try:
        addrs = {info[4][0] for info in socket.getaddrinfo(parsed.hostname, None)}
    except OSError:
        return False
    for addr in addrs:
        ip = ipaddress.ip_address(addr)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True


def unsubscribe(url: str, *, timeout: int = 15) -> bool:
    """GET the one-click URL. Returns True on a 2xx response, False otherwise
    (never raises — a failed unsubscribe attempt shouldn't fail the run)."""
    if not _is_safe_target(url):
        return False
    req = urllib.request.Request(url, method="GET",
                                  headers={"User-Agent": "daily-tech-digest-unsubscribe/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, OSError):
        return False
