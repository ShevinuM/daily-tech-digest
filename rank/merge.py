"""Deterministic candidate-list assembly: merge feed + newsletter items, drop
stale/duplicate items. Genuine judgement (what's worth reading,
ranking, summarizing) is left to the ranking model — this only does the
mechanical qualification work.
"""
from __future__ import annotations

from datetime import datetime

import utils


def assemble(feed_items: list[dict], newsletter_items: list[dict],
             cutoff: datetime) -> list[dict]:
    """Combine feed + newsletter items into one deduped, qualified candidate
    list. Drops anything published before `cutoff`, or not an http(s) URL
    (newsletter links are unwrapped from raw email HTML — a non-http(s)
    scheme should never reach the site as a live <a href>). De-dupes by URL,
    feed items first, so a feed item (which usually carries a body_excerpt)
    wins over a newsletter item pointing at the same URL.

    """
    seen_urls: set[str] = set()
    out: list[dict] = []
    for item in [*feed_items, *newsletter_items]:
        published_at = utils.parse_iso(item.get("published_at", ""))
        if not published_at or published_at < cutoff:
            continue
        url = item.get("url", "")
        if not url.startswith(("http://", "https://")) or url in seen_urls:
            continue
        seen_urls.add(url)
        out.append(item)
    return out
