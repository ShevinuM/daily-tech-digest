"""Deterministic candidate-list assembly: merge feed + newsletter items, drop
paywalled/stale/duplicate items. Genuine judgement (what's worth reading,
ranking, summarizing) is left to the ranking model — this only does the
mechanical qualification work.
"""
from __future__ import annotations

from datetime import datetime

import utils


def assemble(feed_items: list[dict], newsletter_items: list[dict],
             cutoff: datetime) -> list[dict]:
    """Combine feed + newsletter items into one deduped, qualified candidate
    list. Drops anything paywalled (RSS/newsletter links can't reliably tell
    member-only stories apart, and in practice a free alternative is almost
    never findable automatically, so paywalled items are dropped outright
    rather than guessed at), published before `cutoff`, or not an http(s)
    URL (newsletter links are unwrapped from raw email HTML — a non-http(s)
    scheme should never reach the site as a live <a href>). De-dupes by URL,
    feed items first, so a feed item (which usually carries a body_excerpt)
    wins over a newsletter item pointing at the same URL."""
    seen_urls: set[str] = set()
    out: list[dict] = []
    for item in [*feed_items, *newsletter_items]:
        if item.get("paywalled"):
            continue
        published_at = utils.parse_iso(item.get("published_at", ""))
        if not published_at or published_at < cutoff:
            continue
        url = item.get("url", "")
        if not url.startswith(("http://", "https://")) or url in seen_urls:
            continue
        seen_urls.add(url)
        out.append(item)
    return out


def trim_for_selection(items: list[dict], *, excerpt_chars: int = 200) -> list[dict]:
    """Trim candidates to the fields + excerpt length the selection call
    needs, to keep that call's input small (token-efficiency: this list can
    have 50-100+ candidates, so no full body excerpts here)."""
    trimmed = []
    for item in items:
        trimmed.append({
            "url": item["url"],
            "source": item.get("source", ""),
            "title": item.get("title", ""),
            "published_at": item.get("published_at", ""),
            "tags": item.get("tags", []) or [],
            "description": (item.get("description") or item.get("body_excerpt") or "")[:excerpt_chars],
        })
    return trimmed
