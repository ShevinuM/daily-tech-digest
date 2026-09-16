"""Gary Marcus ("Marcus on AI") — public Substack RSS.

Same shape as feeds/bytebytego.py and feeds/pragmatic_engineer.py: a dek in
<description>, the full post in content:encoded. The feed carries no
dc:creator and no <author> element at all, so the byline comes from AUTHOR
rather than the XML.

This is an opinion and criticism blog about the AI industry, not an
engineering blog, and that puts it in tension with two rules in the reading
hub's interests.md: the "Exclude" entry for AI industry/policy/regulation
and funding news, and the "Tech only" rule. It is therefore added as an
ordinary discovery source with no exemptions — it must out-score Hacker
News and ByteByteGo on relevance, and the digest model applies the Exclude
rules on top. Deliberately NOT in never_drop_sources or priority_sources:
those exist for sources whose on-topic-ness is trusted, and the filters are
the whole point here.

Publishes near-daily (~0.95 posts/day), the highest volume of any source in
feeds/.
"""

from __future__ import annotations

import utils

NAME = "gary_marcus"
ENABLED = True

RSS = "https://garymarcus.substack.com/feed"
AUTHOR = "Gary Marcus"
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
