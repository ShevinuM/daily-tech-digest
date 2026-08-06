"""Pool 1 (raw feed output) -> pool 2 (qualified, per-source thresholds and
caps applied). This is the mechanical narrowing step that used to be folded
into the single LLM selection call; see PLAN.md for the rationale.
"""
from __future__ import annotations

from datetime import datetime

from rank import merge


def _source_key(item: dict) -> str:
    """Group by the item's own `source` field, not a feed module's `NAME` —
    `feeds/dev_to.py` sets `source="dev.to"` while `NAME` is `dev_to`, and
    newsletter items use `source="newsletter:<sender>"`. Keying off `NAME`
    here would silently orphan every dev.to item from its config rule."""
    source = item.get("source", "")
    return "newsletter" if source.startswith("newsletter:") else source


def _apply_floors(items: list[dict], rule: dict) -> list[dict]:
    """Each `min_<field>` key in `rule` filters on `<field>`, derived from
    the key name itself — independent of `sort_key`. A rule can sort on one
    signal and floor on another (or floor with no sort_key at all), and
    multiple `min_*` keys all apply. Missing field on an item -> 0."""
    for key, value in rule.items():
        if not key.startswith("min_") or value is None:
            continue
        field = key[len("min_"):]
        items = [i for i in items if (i.get(field) or 0) >= value]
    return items


def build_pool2(feed_items: list[dict], newsletter_items: list[dict],
                 cutoff: datetime, cfg: dict) -> list[dict]:
    pools_cfg = cfg.get("pools", {})
    pool2_cfg = pools_cfg.get("pool2", {})

    candidates = merge.assemble(feed_items, newsletter_items, cutoff)

    groups: dict[str, list[dict]] = {}
    for item in candidates:
        groups.setdefault(_source_key(item), []).append(item)

    out: list[dict] = []
    for key in sorted(groups):
        items = groups[key]
        rule = pool2_cfg.get(key, {})

        sort_key = rule.get("sort_key")
        if sort_key:
            items = sorted(items, key=lambda i: -(i.get(sort_key) or 0))

        items = _apply_floors(items, rule)

        cap = rule.get("cap")
        if cap is not None:
            items = items[:cap]

        out.extend(items)

    for rank, item in enumerate(out):
        item["pool2_rank"] = rank

    return out
