"""ByteByteGo — public Substack RSS.

Same shape as feeds/pragmatic_engineer.py (Substack): a dek in <description>
and the full post in content:encoded, so summaries come from real text.

Two things to know about this feed:

Volume. It publishes near-daily (~0.8 posts/day), several times more often
than the personal blogs. That matters if it is ever moved into
pools.priority_sources, since pinned items spend from the same ~9-item
reading budget.

Roughly one post in ten is course/event marketing ("LAST CALL FOR
ENROLLMENT: ...") rather than editorial. There is no structural way to tell
those apart here — promos and articles carry identical metadata
(dc:creator, enclosure, guid, no categories), and length doesn't separate
them either: the ads run ~6.3-6.6k chars while genuine short posts run
~6.9-7.0k. So they are deliberately NOT filtered in this module; they are
left to compete on relevance and to the digest model's editorial judgement,
which reads the "Exclude" rules in interests.md. Pinning this source would
bypass exactly that and put the ads straight into the digest.
"""

from __future__ import annotations

import utils

NAME = "bytebytego"
ENABLED = True

RSS = "https://blog.bytebytego.com/feed"
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
        out.append(utils.item(
                source=NAME,
                title=utils.rss_field(block, "title"),
                url=utils.rss_field(block, "link"),
                published_at=utils.iso(when),
                author=utils.rss_field(block, "dc:creator"),
                tags=utils.rss_categories(block),
                description=utils.strip_html(utils.rss_field(block, "description"), 400),
                body_excerpt=utils.strip_html(
                    utils.rss_field(block, "content:encoded"), BODY_CHARS
                ),
            ))

    utils.log(
        f"{NAME}: {len(out)} fresh (newest post "
        f"{utils.iso(newest) if newest else 'unknown'})",
        verbose=verbose,
    )
    return out, []
