"""Medium — public RSS tag feeds.

RSS metadata is limited, and Medium 403s our body-fetcher every time
(`config.json`'s `enrich.skip_sources`), so these items are scored/summarized
on their RSS description.

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
            )

    out = sorted(seen.values(), key=lambda x: x["published_at"], reverse=True)
    utils.log(f"{NAME}: {len(out)} fresh", verbose=verbose)
    return out, errors
