"""dev.to — public API, no auth.

The most productive source by a wide margin: exact publish timestamps and
reaction counts, so both the 24h filter and importance ranking are reliable.
"""

from __future__ import annotations

import utils

NAME = "dev_to"
# The item-level `source` field dev.to items actually carry — distinct from
# NAME. feeds.body_fetcher() and rank/pools.py key off this, not NAME.
SOURCE = "dev.to"
ENABLED = True

API = "https://dev.to/api/articles"

# Tag feeds checked in addition to the global top feed. Tune to your stack.
TAGS = ["typescript", "javascript", "node", "react", "devops", "docker", "ai"]

BODY_CHARS = 2500


def fetch(cutoff, *, verbose=False, **_):
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
                source=SOURCE,
                title=a.get("title"),
                url=link,
                published_at=a.get("published_at"),
                author=(a.get("user") or {}).get("username"),
                tags=a.get("tag_list") or [],
                description=(a.get("description") or "").strip(),
                path=a.get("path", "").lstrip("/"),
                reactions=a.get("public_reactions_count", 0),
                comments=a.get("comments_count", 0),
                reading_minutes=a.get("reading_time_minutes"),
            )

    ranked = sorted(seen.values(), key=lambda x: -x.get("reactions", 0))
    utils.log(f"{NAME}: {len(ranked)} fresh", verbose=verbose)
    if errors:
        utils.log(f"{NAME}: {len(errors)} error(s)", verbose=verbose)
    return ranked, errors


def fetch_body(item: dict) -> str | None:
    """`rank/enrich.py`'s per-source hook — used only for items that reach
    pool 2/3, instead of pre-fetching a fixed 25 bodies at feed time.

    Deliberately does NOT collapse whitespace here: `rank/enrich.py`'s
    `clean_for_summary` strips markdown heading/bullet markers by matching
    at the start of a line, and it runs on whatever this returns. Collapsing
    newlines first would destroy those line starts and let raw `##`/`- `
    markdown syntax leak into the LLM prompt."""
    path = item.get("path")
    if not path:
        return None
    data = utils.http_get(f"{API}/{path}", as_json=True)
    return (data.get("body_markdown") or "")[:BODY_CHARS]
