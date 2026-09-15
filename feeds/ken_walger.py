"""Ken W. Alger — public WordPress RSS.

Like The Pragmatic Engineer's feed this embeds the full article in
content:encoded, so summaries can be written from real text, and it carries
real <category> elements for tags.

The feed's dc:creator is the literal WordPress account name ("admin"), which
would render as the byline on the site — so the author is set from AUTHOR
here rather than read out of the XML.
"""

from __future__ import annotations

import utils

NAME = "ken_walger"
ENABLED = True

RSS = "https://www.kenwalger.com/blog/feed/"
AUTHOR = "Ken W. Alger"
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
                author=AUTHOR,
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
