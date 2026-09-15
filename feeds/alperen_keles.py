"""Alperen Keles — public Atom feed (Zola).

Atom, not RSS, so this one goes through utils' atom_* helpers: <entry>
blocks, an ISO <published> date, and the permalink in the alternate link's
href attribute. The entry body is a full post in <content type="html">,
HTML-escaped into XML text — utils.atom_content unescapes that back to HTML
before strip_html turns it into the text we score and summarize on.

No <category> elements in this feed, so items carry no tags.

Posts roughly monthly, so returning zero fresh items on most days is
expected, not a failure.
"""

from __future__ import annotations

import utils

NAME = "alperen_keles"
ENABLED = True

FEED = "https://alperenkeles.com/atom.xml"
BODY_CHARS = 3000


def fetch(cutoff, *, verbose=False, **_):
    try:
        xml = utils.http_get(FEED)
    except Exception as e:  # noqa: BLE001
        return [], [f"{NAME}: {e}"]

    out = []
    newest = None
    for block in utils.atom_entries(xml):
        when = utils.atom_date(block)
        if not when:
            continue
        if newest is None or when > newest:
            newest = when
        if when < cutoff:
            continue
        body = utils.atom_content(block)
        out.append(utils.item(
                source=NAME,
                title=utils.rss_field(block, "title"),
                url=utils.atom_link(block),
                published_at=utils.iso(when),
                author=utils.atom_author(block),
                description=utils.strip_html(body, 400),
                body_excerpt=utils.strip_html(body, BODY_CHARS),
            ))

    utils.log(
        f"{NAME}: {len(out)} fresh (newest post "
        f"{utils.iso(newest) if newest else 'unknown'})",
        verbose=verbose,
    )
    return out, []
