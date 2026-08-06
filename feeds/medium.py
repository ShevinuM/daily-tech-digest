"""Medium — public RSS tag feeds.

Caveat worth knowing before you rely on this: RSS cannot distinguish
member-only stories from free ones, so every item is flagged
`paywalled: true`. Under `config.json`'s `pools.allow_paywalled`, these items
still enter pool 2 — the site renders a "member-only" badge instead of
dropping them outright — but they're capped hard at `pool3.max_per_source`
(3) since Medium also 403s our body-fetcher every time (`config.json`'s
`enrich.skip_sources`), so they're only ever scored/summarized on their
short RSS description.

Set ENABLED = False (or delete this file) to skip it.
"""

from __future__ import annotations

import re

import utils

NAME = "medium"
ENABLED = True

TAGS = [
    "programming",
    "software-engineering",
    "artificial-intelligence",
    "developer-productivity",
]


def fetch(cutoff, *, verbose=False, **_):
    seen: dict[str, dict] = {}
    errors = []

    for tag, xml, err in utils.parallel(
        lambda t: utils.http_get(f"https://medium.com/feed/tag/{t}"), TAGS, workers=4
    ):
        if err:
            errors.append(f"{NAME}: tag {tag} -> {err}")
            continue
        for block in utils.rss_items(xml):
            when = utils.rss_date(block)
            if not when or when < cutoff:
                continue
            link = utils.clean_url(utils.rss_field(block, "link") or "")
            if not link or link in seen:
                continue
            desc = utils.rss_field(block, "description") or ""
            snip = re.search(r"medium-feed-snippet[^>]*>(.*?)</p>", desc, re.S)
            seen[link] = utils.item(
                source=NAME,
                title=utils.rss_field(block, "title"),
                url=link,
                published_at=utils.iso(when),
                author=utils.rss_field(block, "dc:creator"),
                tags=utils.rss_categories(block),
                description=utils.strip_html(snip.group(1) if snip else desc, 400),
                paywalled=True,
                paywall_note="Medium RSS cannot identify member-only stories; "
                             "verify a free version before including.",
            )

    out = sorted(seen.values(), key=lambda x: x["published_at"], reverse=True)
    utils.log(f"{NAME}: {len(out)} fresh (all paywall-flagged)", verbose=verbose)
    return out, errors
