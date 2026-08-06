"""LLM prompt template, as a Python string constant (not a committed .md
instruction file — this is a template consumed programmatically, unit
testable, and versioned with the code that calls it).

One call per run. Freshness, topic filtering, dedupe,
topic relevance, and summarization are all done in Python before this ever
runs (rank/pools.py, rank/relevance.py, rank/summarize.py) — the only thing
left for the model is genuine editorial judgement: which of the pre-scored
candidates to keep, how to group and title them, and turning each one's
extractive summary into prose. No item carries a `url` or `published_at`
into the prompt at all; `main.py`'s `_reconcile_digest` keys the model's
response back to our own data by index, so a hallucinated or
prompt-injected URL can never reach the public site.
"""
from __future__ import annotations

import json
import re

import utils

_URL_RE = re.compile(r"https?://\S+|\bwww\.\S+", re.I)

DIGEST_INSTRUCTIONS = """\
You are writing a developer's daily tech reading digest. The candidates
below have already been fetched, filtered for freshness/topic, and
ranked by relevance — your job is editorial judgement and prose, not
filtering.

Below is the reader's Interests & Rules file (plain English — the
authoritative source for priorities, stack, and tone) and {candidate_count}
pre-scored candidate items, each with an extractive summary already pulled
from its source text.

Your job:
1. Pick the best {target_count} items for today's digest (±1 is fine if
   there genuinely aren't enough good candidates — never pad with low-value
   items to hit the count), rank them best-first.
2. Group them into sections you invent from the content (a topic grouping
   like "Agent & AI-Engineering Craft" or "Developer Tooling & Craft"), each
   with a short heading carrying one leading emoji.
3. Rewrite each chosen item's extractive summary into flowing prose,
   ~120-200 words, explaining what it is and why it matters to a developer
   growing their craft. Stay accurate to the given summary — invent no
   specifics you can't see in it.
4. Write one `intro` line: just today's date-appropriate hook naming the
   dominant theme(s). No meta-commentary ("curated", "ranked by priority",
   "N most worth-reading", read-time estimates, "every item links to its
   source").

Respect the Interests & Rules file's topic priorities, "My stack" filter,
"Dial up" / "Dial down" lists, the AI/LLM share cap, and its preference for
accessible, practical writing over dense academic or deep infra-internals
pieces.

=== INTERESTS & RULES ===
{interests}
=== END INTERESTS & RULES ===

=== CANDIDATES (JSON array) ===
{items}
=== END CANDIDATES ===

Respond with ONLY JSON shaped exactly like:
{{"intro": "<one line>", "sections": [{{"heading": "<emoji> <title>", "items": [
  {{"i": <int index from the candidate list>, "summary": "<your rewritten prose>"}}
]}}]}}
Do not include url, title, source, or tags in your response — those are
filled in from our own data afterward, keyed by `i`. No prose, no markdown
fences, just the JSON object.
"""


def _scrub(text: str) -> str:
    """Last line of defence for the no-URL invariant. Every string that
    reaches the model passes through here, regardless of which upstream
    stage produced it — title, tag, and summary all have paths that bypass
    rank/enrich.clean_for_summary() (e.g. a newsletter link with no anchor
    text, or a pool-3 item whose enrichment failed and fell back to a raw
    description)."""
    return re.sub(r"\s{2,}", " ", _URL_RE.sub(" ", text or "")).strip()


def build_digest_prompt(interests_text: str, items: list[dict], target_count: int) -> str:
    """`items` are pool-3 items, already carrying a `summary` (from
    rank/summarize.py). Builds the minimal per-item payload — no url, no
    published_at — indexed contiguously from 0; `main.py`'s
    `_reconcile_digest` maps the model's response back to these same items
    by that index."""
    payload = []
    for i, it in enumerate(items):
        title = _scrub(it.get("title", "")) or utils.slug_words(it.get("url", "")) or "Untitled"
        tags = [_scrub(t) for t in (it.get("tags") or [])]
        summary = _scrub(it.get("summary", ""))
        payload.append({"i": i, "title": title, "source": it.get("source", ""),
                         "tags": tags, "summary": summary})
    return DIGEST_INSTRUCTIONS.format(
        target_count=target_count,
        candidate_count=len(payload),
        interests=interests_text.strip(),
        items=json.dumps(payload, ensure_ascii=False),
    )
