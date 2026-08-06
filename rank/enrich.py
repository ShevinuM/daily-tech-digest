"""Fetch + extract article body text for pool items that don't already carry
usable text to score/summarize from (mainly Hacker News, whose items are
title + score + url only — see PLAN.md Finding 1). The result is cached on
the item as `article_text`, so the same fetch feeds both the pool-2
relevance-scoring pass and the pool-3 sumy summarization pass; `ensure_text`
skips anything that already has one, making it safe to call twice.
"""
from __future__ import annotations

import re

import trafilatura

import feeds
import utils

_CODE_FENCE_RE = re.compile(r"```.*?```", re.S)
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_BARE_URL_RE = re.compile(r"https?://\S+")
_HEADING_RE = re.compile(r"^#{1,6}\s+", re.M)
_LIST_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+", re.M)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
# Only *, **, *** and backticks are unconditionally markdown emphasis.
# Underscores are ambiguous with identifiers (snake_case, __init__), so
# they're only stripped at a non-word boundary — (?<!\w)_+ / _+(?!\w) — which
# still kills __bold__ / _italic_ while leaving snake_case_name alone.
_EMPHASIS_RE = re.compile(r"\*{1,3}|`|(?<!\w)_+|_+(?!\w)")


def clean_for_summary(text: str) -> str:
    """Strip markdown/URL syntax before text reaches sumy. Order matters:
    code fences and images are removed whole, before the generic link/
    emphasis stripping runs — otherwise their inner brackets and asterisks
    leak through as prose. This is a hard requirement, not cosmetic: raw
    dev.to markdown fed to sumy produced a summary containing a live
    `[text](url)` link, and no URL is allowed to reach the LLM prompt."""
    text = (text or "").replace("\x00", "")
    text = _CODE_FENCE_RE.sub(" ", text)
    text = _IMAGE_RE.sub(" ", text)
    text = _MD_LINK_RE.sub(r"\1", text)
    text = _BARE_URL_RE.sub(" ", text)
    text = _HEADING_RE.sub("", text)
    text = _LIST_BULLET_RE.sub("", text)

    # Inline code spans hold identifiers (`max_per_source`, `__init__`) whose
    # underscores and asterisks are not markdown emphasis. Park them before
    # the emphasis pass runs, then restore verbatim. Bare URLs are already
    # gone by this point, so parking can't smuggle one past the invariant.
    spans: list[str] = []

    def _park(m):
        spans.append(m.group(1))
        return f"\x00{len(spans) - 1}\x00"

    text = _INLINE_CODE_RE.sub(_park, text)
    text = _EMPHASIS_RE.sub("", text)
    text = re.sub(r"\x00(\d+)\x00", lambda m: spans[int(m.group(1))], text)
    return re.sub(r"\s+", " ", text).strip()


def _resolve(item: dict) -> str:
    fetcher = feeds.body_fetcher(item.get("source", ""))
    if fetcher:
        try:
            text = fetcher(item)
        except Exception as e:  # noqa: BLE001 - a source-specific hook failing
            # (dev.to 429s are routine) must fall through to the generic
            # path, not abandon the item's text altogether.
            utils.log(f"enrich: {item.get('source')} body hook failed ({e}); "
                      f"falling back to generic extraction", verbose=True)
            text = None
        if text:
            return text

    excerpt = item.get("body_excerpt")
    if excerpt:
        return excerpt

    html = utils.http_get(item["url"])
    extracted = trafilatura.extract(
        html, include_comments=False, include_tables=False, favor_precision=True)
    return extracted or ""


def ensure_text(items: list[dict], *, workers: int = 8, skip_sources: tuple = (),
                 verbose: bool = False) -> list[str]:
    """Sets `article_text` on every item in `items` that doesn't already
    have one. Failures are non-fatal: a fetch/extract error for one item is
    recorded and the item is left without `article_text` (downstream stages
    fall back to `description`/`title`), never raised.

    `skip_sources` excludes a source known to always block extraction (e.g.
    Medium 403s our fetcher every time — see PLAN.md D10) from even being
    attempted, rather than paying for a futile fetch and recording an error
    every run."""
    skip = set(skip_sources)
    targets = [i for i in items
               if not i.get("article_text") and i.get("source") not in skip]
    errors: list[str] = []
    if not targets:
        return errors

    # Prime feeds.body_fetcher's module cache on this thread before the pool
    # starts — otherwise every worker thread races to build it on first use.
    feeds.body_fetcher("")

    got = 0
    for item, text, err in utils.parallel(_resolve, targets, workers=workers):
        if err:
            errors.append(f"enrich {item.get('url', '?')}: {err}")
            continue
        if text:
            item["article_text"] = clean_for_summary(text)
            got += 1
    utils.log(f"enrich: {got}/{len(targets)} article_text resolved", verbose=verbose)
    return errors
