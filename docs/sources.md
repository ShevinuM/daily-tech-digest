# Sources

## Adding or removing a feed source

Sources are plug-ins. Drop a file in `feeds/` to add one; delete it to
remove one. Nothing else needs editing.

```python
# feeds/lobsters.py
"""Lobsters — public JSON API."""
import utils

NAME = "lobsters"
ENABLED = True          # set False to disable without deleting the file

def fetch(cutoff, *, verbose=False, **opts):
    """Return (items, errors). Items come from utils.item()."""
    stories = utils.http_get("https://lobste.rs/hottest.json", as_json=True)
    out = []
    for s in stories:
        when = utils.parse_iso(s["created_at"])
        if not when or when < cutoff:
            continue
        out.append(utils.item(
            source=NAME, title=s["title"], url=s["url"],
            published_at=utils.iso(when), author=s["submitter_user"],
            tags=s.get("tags", []), score=s.get("score", 0),
        ))
    return out, []
```

```bash
python3 main.py feeds                  # confirm it was picked up
python3 main.py fetch --only lobsters  # try it in isolation
```

To pause a source without removing it, set `ENABLED = False` in its module.
It drops out of discovery — and out of `--only`, so `fetch --only medium`
can't resurrect it — while the module, its tests, and its `pools`/`relevance`
config entries all stay put. Re-enabling is that one flag.

**Currently paused:** `dev_to`, `medium` (since 2026-09-16). With both off the
candidate pool is Hacker News, the priority blogs, and newsletters.

Raising inside `fetch` is fine — it's recorded as an error and the run
continues with the other feeds.

## Priority sources

Most sources are *discovery* — dev.to, Hacker News, Medium — where the point
is to surface what's worth reading out of a firehose. A handful are blogs read
directly, and those shouldn't have to win a relevance contest to appear.
Listing a source in `config.json` under `pools.priority_sources` pins it:

```json
"pools": {
  "priority_sources": [
    "jason_wei", "ken_walger", "pragmatic_engineer",
    "alperen_keles", "martin_fowler"
  ]
}
```

A pinned item **skips**:

- the non-tech drop in `rank/relevance.py` (a career or sports essay from a
  blog you follow is still something you want to read),
- the pool-3 top-N cut and per-source cap — and it doesn't consume a
  `pool3.size` slot either, so the 25-deep merit pool stays 25 deep,
- the LLM's editorial selection: the prompt marks it must-include, and
  `main.py:_ensure_pinned` inserts it afterwards if the model ignored that.

A pinned item **still faces** the freshness cutoff (`digest.freshness_hours`)
and URL dedupe, like everything else, and it spends from the same
`digest.target_read_minutes` budget — so pinned items push discovery items
out of the digest rather than lengthening it.

Matching is on an item's `source` field, so a post that arrives by newsletter
instead (`source="newsletter:<sender>"`) isn't pinned. When both arrive, the
dedupe in `rank/merge.py` keeps the feed copy, which is.

`main.py pools` marks pinned rows `PIN` and prints the count, and
`main.py pools --json` puts a boolean `priority` on every pool-3 entry.

### The middle tier: `relevance.never_drop_sources`

Between "competes on merit" and "pinned" there's a source you trust to be
*on topic* but still want ranked and editorially filtered. That's
`relevance.never_drop_sources` — it exempts a source from the non-tech drop
and nothing else.

`bytebytego` is there. Measured on 2026-09-16, the drop rule misfired on 4 of
its 20 posts — git internals, application networking, model distillation and
inference runtimes all scored `non_tech_sim > raw_topic_sim` and were
silently discarded. It is deliberately *not* pinned: roughly one ByteByteGo
post in ten is course marketing, indistinguishable from editorial at fetch
time (identical `dc:creator`/`enclosure`/`guid`, and length doesn't separate
them — ads run ~6.3-6.6k chars against genuine short posts at ~6.9-7.0k), so
it needs to stay in front of the digest model, which is the only stage that
can recognise an ad.

### Paywalled sources

A source with a paid tier needs one more thing from its feed module: a way
to tell a free post from a gated one, because the platforms ship both in the
same public feed.

`ed_zitron` is the case in the tree. Ghost gates the content *before* the
RSS is rendered and leaves no flag, category or element behind — a paid post
comes through as either a teaser truncated at the paywall break (with Ghost's
own `<!--members-only-->` marker sliced off) or an item whose
`content:encoded` is empty. So the module infers it from the body: anything
under `MIN_BODY_CHARS` is a teaser or an empty shell, and a body that runs
longer but carries one of `PAYWALL_PHRASES` (the author's own sign-off
before the break, which survives as ordinary prose) is dropped too.

The threshold is deliberately loose — this blog publishes long essays, so it
sits far from anything genuine. Where the two could be confused, drop: a free
post lost is one item missing from a digest, a premium teaser kept is a dead
link the reader clicks. Each run logs the drop count
(`ed_zitron: N fresh, M premium dropped`), so a change in the blog's
publishing shape shows up in the run log rather than as a quietly empty feed.
