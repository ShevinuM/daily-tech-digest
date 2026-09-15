"""Martin Fowler — public Atom feed (the site's master feed).

Atom, like feeds/alperen_keles.py, but two things differ and both matter:

Entries carry no <published>, only <updated>, so utils.atom_date falls back
to it and freshness here means "last edited". That's mostly right for this
site — Fowler publishes long articles in installments, and a new installment
is genuinely new content — but it does mean an article edited on consecutive
days can appear in the digest on consecutive days, since the freshness
window is the only thing deduping across runs.

Tags come from Atom `term` attributes rather than element text (see
utils.atom_categories). The feed mixes real post-type tags in with site
plumbing, so SKIP_TERMS drops the latter — those tags reach the digest site
as public tag pages, and "skip-home-page" is not a topic anyone browses by.

Posts a few times a week, so a day with nothing fresh is normal.
"""

from __future__ import annotations

import utils

NAME = "martin_fowler"
ENABLED = True

FEED = "https://martinfowler.com/feed.atom"
BODY_CHARS = 3000

# Rendering directives for martinfowler.com itself, not subjects.
SKIP_TERMS = {"skip-home-page"}


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
        tags = [t for t in utils.atom_categories(block) if t not in SKIP_TERMS]
        out.append(utils.item(
                source=NAME,
                title=utils.rss_field(block, "title"),
                url=utils.atom_link(block),
                published_at=utils.iso(when),
                author=utils.atom_author(block),
                tags=tags,
                description=utils.strip_html(body, 400),
                body_excerpt=utils.strip_html(body, BODY_CHARS),
            ))

    utils.log(
        f"{NAME}: {len(out)} fresh (newest post "
        f"{utils.iso(newest) if newest else 'unknown'})",
        verbose=verbose,
    )
    return out, []
