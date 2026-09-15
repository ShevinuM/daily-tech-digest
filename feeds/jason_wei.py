"""Jason Wei — public Squarespace RSS.

Squarespace serves the feed off the blog page itself (`?format=rss`) and puts
the whole post in <description>; there is no content:encoded here, so the
summary text and the short dek both come from that one field under different
char budgets. No <category> elements either, so items carry no tags.

Posts roughly monthly, so returning zero fresh items on most days is expected,
not a failure.
"""

from __future__ import annotations

import utils

NAME = "jason_wei"
ENABLED = True

RSS = "https://www.jasonwei.net/blog?format=rss"
BODY_CHARS = 3000


def fetch(cutoff, *, verbose=False, **_):
    try:
        xml = utils.http_get(RSS)
    except Exception as e:  # noqa: BLE001
        return [], [f"{NAME}: {e}"]

    out = []
    newest = None
    for block in utils.rss_items(xml):
        when = utils.rss_date(block)
        if not when:
            continue
        if newest is None or when > newest:
            newest = when
        if when < cutoff:
            continue
        body = utils.rss_field(block, "description") or ""
        out.append(utils.item(
                source=NAME,
                title=utils.rss_field(block, "title"),
                url=utils.rss_field(block, "link"),
                published_at=utils.iso(when),
                author=utils.rss_field(block, "dc:creator"),
                description=utils.strip_html(body, 400),
                body_excerpt=utils.strip_html(body, BODY_CHARS),
            ))

    utils.log(
        f"{NAME}: {len(out)} fresh (newest post "
        f"{utils.iso(newest) if newest else 'unknown'})",
        verbose=verbose,
    )
    return out, []
