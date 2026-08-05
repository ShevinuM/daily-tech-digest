"""Shared helpers for feed modules. No feed-specific logic lives here."""

from __future__ import annotations

import json
import re
import ssl
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) digest-fetcher/2.0"
TIMEOUT = 20

# Domains that habitually paywall. Items from these are flagged, not dropped —
# the digest applies its own no-paywall rule.
PAYWALL_HINTS = (
    "medium.com", "towardsdatascience.com", "levelup.gitconnected.com",
    "betterprogramming.pub", "wsj.com", "ft.com", "nytimes.com",
    "theinformation.com", "bloomberg.com", "economist.com", "newyorker.com",
)

_ctx = ssl.create_default_context()


def log(msg: str, *, verbose: bool = True) -> None:
    if verbose:
        print(f"[digest] {msg}", file=sys.stderr)


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

def http_get(url: str, *, as_json: bool = False, timeout: int = TIMEOUT):
    """One GET. Raises on failure so callers can record the reason."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout, context=_ctx) as r:
        raw = r.read()
    text = raw.decode("utf-8", "replace")
    return json.loads(text) if as_json else text


def parallel(fn, jobs, *, workers: int = 8):
    """Map fn over jobs concurrently. Yields (job, result, error) per job.

    Never raises — a failing job comes back with error set, so one bad source
    can't take down a run.
    """
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fn, j): j for j in jobs}
        for fut in as_completed(futures):
            job = futures[fut]
            try:
                yield job, fut.result(), None
            except Exception as e:  # noqa: BLE001 - deliberately broad
                yield job, None, e


# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------

def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat((s or "").replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def parse_rfc822(s: str) -> datetime | None:
    """RSS pubDate. Always returns tz-aware, assuming UTC when unspecified."""
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        return None
    if d is None:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# RSS / HTML
# --------------------------------------------------------------------------

def rss_items(xml: str) -> list[str]:
    return [m.group(1) for m in re.finditer(r"<item>(.*?)</item>", xml or "", re.S)]


def rss_field(block: str, tag: str) -> str | None:
    m = re.search(
        rf"<{tag}[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{tag}>", block or "", re.S
    )
    return m.group(1).strip() if m else None


def rss_date(block: str) -> datetime | None:
    return parse_rfc822(rss_field(block, "pubDate"))


def rss_categories(block: str, limit: int = 6) -> list[str]:
    return re.findall(
        r"<category>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</category>", block or ""
    )[:limit]


def strip_html(s: str, limit: int) -> str:
    """Tags first, whitespace second.

    The other order leaves a double space wherever a tag was removed - that was
    a real bug, caught by tests/test_offline.py.
    """
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()[:limit]


def clean_url(url: str) -> str:
    """Drop RSS tracking params."""
    return (url or "").split("?source=")[0].strip()


def is_paywalled(url: str) -> bool:
    return any(h in (url or "") for h in PAYWALL_HINTS)


# --------------------------------------------------------------------------
# Item shape
# --------------------------------------------------------------------------

ITEM_FIELDS = (
    "source", "title", "url", "published_at", "author", "tags",
    "description", "paywalled", "body_excerpt",
)


def item(*, source, title, url, published_at, author=None, tags=None,
         description="", paywalled=None, body_excerpt=None, **extra) -> dict:
    """Build one normalised item. Every feed module returns these.

    `extra` carries source-specific signals (reactions, score, discussion_url)
    that the digest can use for ranking.
    """
    url = clean_url(url)
    d = {
        "source": source,
        "title": (title or "").strip(),
        "url": url,
        "published_at": published_at,
        "author": author,
        "tags": tags or [],
        "description": description or "",
        "paywalled": is_paywalled(url) if paywalled is None else bool(paywalled),
        "body_excerpt": body_excerpt,
    }
    d.update(extra)
    return d
