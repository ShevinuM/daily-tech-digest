"""Ed Zitron ("Where's Your Ed At") — public Ghost RSS, free posts only.

Ghost, not Substack, and that changes what the feed can tell us. The shape
is otherwise familiar: a dek in <description>, the post HTML in
content:encoded, byline in <dc:creator>, tags as <category>.

Like feeds/gary_marcus.py this is opinion and criticism about the AI
industry rather than an engineering blog, so it goes in as an ordinary
discovery source with no exemptions — not in pools.priority_sources, not in
relevance.never_drop_sources. It has to out-score Hacker News on relevance
and survive the digest model's reading of the "Exclude" rules in
interests.md. Those filters are the point, not an obstacle.

Premium posts
-------------
This blog has a paid tier, and Ghost puts paid posts in the *public* feed
alongside free ones — gated, not omitted. Only the free ones are wanted
here, and the gating has to be recognised from the feed itself.

Ghost's content gating (TryGhost/Ghost, post-gating.js) runs before the RSS
is rendered and leaves exactly two shapes behind for a reader with no
member session:

  - the post has an explicit paywall break: the HTML is truncated at it and
    the `<!--members-only-->` marker itself is sliced off, so what reaches
    content:encoded is a teaser of a paragraph or two with *no marker left
    to match on*;
  - the post has no paywall break: html, plaintext and excerpt are all set
    to "", so content:encoded arrives empty.

Neither case sets a flag, a category or an element of its own — Ghost's RSS
generator (generate-feed.js) writes the same item shape for a gated post as
for a free one. So the only signal available is the body, and gating is
inferred from length: below MIN_BODY_CHARS is a teaser or an empty shell,
not an essay. Zitron writes long — posts run many thousands of words — so
the threshold sits far from anything genuine and drops the ambiguous middle
rather than risking a paywalled item in the digest. A free post short
enough to trip it is a loss we accept; a premium teaser in the digest is a
dead link for the reader.

PAYWALL_PHRASES is a second, independent check for the case where a teaser
runs long: Ghost strips its own marker, but the *author's* sign-off before
the break ("this post is for paying subscribers", "subscribe to read the
rest") survives as ordinary prose and is matched here. It is checked
against the teaser text only, so an essay merely discussing subscriptions
would have to say one of these phrases within its first BODY_CHARS to be
caught — and the phrases are specific enough that it won't.

Both checks are counted and logged per run, so a change in the blog's
publishing shape shows up as a count that stops matching reality instead of
as a silently empty feed.
"""

from __future__ import annotations

import utils

NAME = "ed_zitron"
ENABLED = True

RSS = "https://www.wheresyoured.at/rss/"
BODY_CHARS = 3000

# Below this many characters of stripped body text, an item is a gated
# teaser or an empty shell rather than a post. See the module docstring.
MIN_BODY_CHARS = 1200

# Matched case-insensitively against the teaser text of an item that is long
# enough to pass MIN_BODY_CHARS. Ghost removes its own paywall marker, so
# these target the author-written line that precedes the break.
PAYWALL_PHRASES = (
    "this post is for paying subscribers",
    "this post is for subscribers",
    "this post is for paid subscribers",
    "for paying subscribers only",
    "for premium subscribers",
    "subscribe to read the rest",
    "subscribe to keep reading",
    "upgrade your subscription",
    "become a premium subscriber",
)


def _is_premium(body: str) -> bool:
    """True when the body looks gated rather than published in full."""
    if len(body) < MIN_BODY_CHARS:
        return True
    lowered = body.lower()
    return any(phrase in lowered for phrase in PAYWALL_PHRASES)


def fetch(cutoff, *, verbose=False, **_):
    try:
        xml = utils.http_get(RSS)
    except Exception as e:  # noqa: BLE001
        return [], [f"{NAME}: {e}"]

    out = []
    newest = None
    premium = 0
    for block in utils.rss_items(xml):
        when = utils.rss_date(block)
        if not when:
            continue
        if newest is None or when > newest:
            newest = when
        if when < cutoff:
            continue
        body = utils.strip_html(utils.rss_field(block, "content:encoded"), BODY_CHARS)
        if _is_premium(body):
            premium += 1
            continue
        out.append(utils.item(
                source=NAME,
                title=utils.rss_field(block, "title"),
                url=utils.rss_field(block, "link"),
                published_at=utils.iso(when),
                author=utils.rss_field(block, "dc:creator"),
                tags=utils.rss_categories(block),
                description=utils.strip_html(utils.rss_field(block, "description"), 400),
                body_excerpt=body,
            ))

    utils.log(
        f"{NAME}: {len(out)} fresh, {premium} premium dropped (newest post "
        f"{utils.iso(newest) if newest else 'unknown'})",
        verbose=verbose,
    )
    return out, []
