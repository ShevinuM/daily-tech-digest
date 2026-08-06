"""LLM prompt templates, as Python string constants (not committed .md
instruction files — this is a template consumed programmatically, unit
testable, and versioned with the code that calls it).

Two small batched calls per run, not one call per item:
  1. SELECTION — given trimmed metadata for every qualified candidate, pick
     and rank the target count, assigning each a section.
  2. SUMMARY — given full detail for just the selected items (already
     grouped by section), write the prose: an intro line, section headings,
     and a summary per item.
Splitting this way means the larger candidate list (which can be 50-100+
items) never carries a full body excerpt through the model, only the
already-narrowed selection does.
"""
from __future__ import annotations

import json

SELECTION_INSTRUCTIONS = """\
You are selecting items for a developer's daily tech reading digest.

Below is the reader's Interests & Rules file (plain English — it is the
authoritative source for what belongs in the digest; follow it exactly) and
a list of candidate items already filtered to be fresh (published within
the last 24h) and not paywalled.

Your job: pick the {target_count} most important items (±1 is fine if there
genuinely aren't enough good candidates — never pad with low-value items to
hit the count), rank them best-first, and assign each one a short section
name (a topic grouping like "Agent & AI-Engineering Craft" or "Developer
Tooling & Craft" — invent sensible section names from the content, don't
force every item into a fixed list).

Hard requirements:
- Tech only. Drop anything that is general world news, politics, sports, or
  not genuinely about software/tech/AI.
- Respect the Interests & Rules file's topic priorities, "My stack" filter,
  "Dial up" / "Dial down" lists, and every bullet under "Rules" (including
  any cap on AI/LLM share of the digest) as hard constraints.
- When there are more good candidates than slots, keep the most important
  ones — never drop a high-value item to force section balance.
- Prefer accessible, practical, hands-on writing over dense academic or
  deep infra-internals pieces, per the Rules.

=== INTERESTS & RULES ===
{interests}
=== END INTERESTS & RULES ===

=== CANDIDATES (JSON array) ===
{candidates}
=== END CANDIDATES ===

Respond with ONLY a JSON array, best-first, of objects shaped exactly like:
[{{"url": "<the exact url from the candidate list>", "section": "<short section name>"}}]
No prose, no markdown fences, just the JSON array.
"""

SUMMARY_INSTRUCTIONS = """\
You are writing the prose for a developer's daily tech reading digest. The
items below have already been selected and grouped into sections — do not
add, remove, or re-group items. Your job is purely to write:

- `intro`: a single minimal line — just today's date-appropriate hook naming
  the dominant theme(s). No meta-commentary ("curated", "ranked by
  priority", "N most worth-reading", read-time estimates, "every item links
  to its source") — just what's notable today, one line.
- for each section, a `heading` (a short, punchy title with one leading
  emoji) and for each item a `summary`: substantive, ~120-200 words,
  explaining what it is and why it matters to a developer growing their
  craft. Do not invent specifics you can't see in the item's description —
  stay accurate to what's given.

=== SELECTED ITEMS, GROUPED BY SECTION (JSON) ===
{grouped}
=== END SELECTED ITEMS ===

Respond with ONLY JSON shaped exactly like:
{{"intro": "<one line>", "sections": [{{"heading": "<emoji> <title>", "items": [
  {{"url": "<exact url>", "title": "<exact title>", "source": "<exact source>",
    "publishedAt": "<exact published_at>", "tags": [<exact tags>], "summary": "<your summary>"}}
]}}]}}
Preserve the given section order and item order within each section. No
prose, no markdown fences, just the JSON object.
"""


def build_selection_prompt(interests_text: str, candidates: list[dict], target_count: int) -> str:
    return SELECTION_INSTRUCTIONS.format(
        target_count=target_count,
        interests=interests_text.strip(),
        candidates=json.dumps(candidates, ensure_ascii=False),
    )


def build_summary_prompt(grouped_sections: list[dict]) -> str:
    return SUMMARY_INSTRUCTIONS.format(
        grouped=json.dumps(grouped_sections, ensure_ascii=False),
    )
