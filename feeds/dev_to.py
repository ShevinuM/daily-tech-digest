"""dev.to — public API, no auth.

The most productive source by a wide margin: exact publish timestamps and
reaction counts, so both the 24h filter and importance ranking are reliable.
"""

from __future__ import annotations

import utils

NAME = "dev_to"
ENABLED = True

API = "https://dev.to/api/articles"

# Tag feeds checked in addition to the global top feed. Tune to your stack.
TAGS = ["typescript", "javascript", "node", "react", "devops", "docker", "ai"]

# How many top-ranked articles get a full body fetched for accurate summaries.
BODY_LIMIT = 25
BODY_CHARS = 2500


def fetch(cutoff, *, verbose=False, want_bodies=True, **_):
    urls = [
        f"{API}?top=1&per_page=100&page=1",
        f"{API}?top=1&per_page=100&page=2",
    ] + [f"{API}?tag={t}&top=1&per_page=30" for t in TAGS]

    seen: dict[str, dict] = {}
    errors = []

    for url, data, err in utils.parallel(
        lambda u: utils.http_get(u, as_json=True), urls
    ):
        if err:
            errors.append(f"{NAME}: {url} -> {err}")
            continue
        for a in data or []:
            when = utils.parse_iso(a.get("published_at"))
            if not when or when < cutoff:
                continue
            link = a.get("url")
            if not link or link in seen:
                continue
            seen[link] = utils.item(
                source="dev.to",
                title=a.get("title"),
                url=link,
                published_at=a.get("published_at"),
                author=(a.get("user") or {}).get("username"),
                tags=a.get("tag_list") or [],
                description=(a.get("description") or "").strip(),
                paywalled=False,
                path=a.get("path", "").lstrip("/"),
                reactions=a.get("public_reactions_count", 0),
                comments=a.get("comments_count", 0),
                reading_minutes=a.get("reading_time_minutes"),
            )

    ranked = sorted(seen.values(), key=lambda x: -x.get("reactions", 0))
    utils.log(f"{NAME}: {len(ranked)} fresh", verbose=verbose)

    if want_bodies and ranked:
        _attach_bodies(ranked[:BODY_LIMIT], errors, verbose)

    if errors:
        utils.log(f"{NAME}: {len(errors)} error(s)", verbose=verbose)
    return ranked, errors


def _attach_bodies(targets, errors, verbose):
    """Pull article bodies so the digest can summarise from real text."""
    import re

    def body(it):
        d = utils.http_get(f"{API}/{it['path']}", as_json=True)
        return re.sub(r"\s+", " ", d.get("body_markdown") or "")[:BODY_CHARS]

    got = 0
    for it, text, err in utils.parallel(body, targets):
        if err:
            errors.append(f"{NAME} body {it.get('path')}: {err}")
            continue
        it["body_excerpt"] = text
        got += 1
    utils.log(f"{NAME}: {got} bodies", verbose=verbose)
