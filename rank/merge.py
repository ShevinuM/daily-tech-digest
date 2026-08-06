"""Deterministic candidate-list assembly: merge feed + newsletter items, drop
paywalled/stale/duplicate items. Genuine judgement (what's worth reading,
ranking, summarizing) is left to the ranking model — this only does the
mechanical qualification work.
"""
from __future__ import annotations

from datetime import datetime

import utils


def assemble(feed_items: list[dict], newsletter_items: list[dict],
             cutoff: datetime, *, allow_paywalled: bool = False) -> list[dict]:
    """Combine feed + newsletter items into one deduped, qualified candidate
    list. Drops anything published before `cutoff`, or not an http(s) URL
    (newsletter links are unwrapped from raw email HTML — a non-http(s)
    scheme should never reach the site as a live <a href>). De-dupes by URL,
    feed items first, so a feed item (which usually carries a body_excerpt)
    wins over a newsletter item pointing at the same URL.

    Paywalled items are dropped unless `allow_paywalled` is set (config's
    `pools.allow_paywalled` — see PLAN.md Decision A): RSS/newsletter links
    can't reliably tell member-only stories apart, and in practice a free
    alternative is almost never findable automatically, so by default they're
    dropped outright rather than guessed at."""
    seen_urls: set[str] = set()
    out: list[dict] = []
    for item in [*feed_items, *newsletter_items]:
        if item.get("paywalled") and not allow_paywalled:
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
