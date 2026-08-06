"""Hacker News — Firebase API, no auth.

Scans the top stories and keeps fresh ones above a score floor. The HN
discussion URL is always included.
"""

from __future__ import annotations

from datetime import datetime, timezone

import utils

NAME = "hacker_news"
ENABLED = True

TOP = "https://hacker-news.firebaseio.com/v0/topstories.json"
ITEM = "https://hacker-news.firebaseio.com/v0/item/{}.json"

SCAN = 90        # how many of the top stories to inspect
MIN_SCORE = 40   # ignore anything below this many points


def fetch(cutoff, *, verbose=False, **_):
    try:
        ids = utils.http_get(TOP, as_json=True)[:SCAN]
    except Exception as e:  # noqa: BLE001
        return [], [f"{NAME}: topstories -> {e}"]

    out, errors = [], []
    for sid, story, err in utils.parallel(
        lambda i: utils.http_get(ITEM.format(i), as_json=True), ids, workers=12
    ):
        if err:
            errors.append(f"{NAME}: item {sid} -> {err}")
            continue
        if not story or story.get("type") != "story":
            continue
        if story.get("dead") or story.get("deleted"):
            continue
        ts = story.get("time")
        if not ts:
            continue
        when = datetime.fromtimestamp(ts, tz=timezone.utc)
        if when < cutoff or (story.get("score") or 0) < MIN_SCORE:
            continue

        discussion = f"https://news.ycombinator.com/item?id={story['id']}"
        out.append(utils.item(
            source=NAME,
            title=story.get("title"),
            url=story.get("url") or discussion,
            published_at=utils.iso(when),
            author=story.get("by"),
            discussion_url=discussion,
            score=story.get("score", 0),
            comments=story.get("descendants", 0),
        ))

    out.sort(key=lambda x: -x["score"])
    utils.log(f"{NAME}: {len(out)} fresh scoring >= {MIN_SCORE}", verbose=verbose)
    return out, errors
