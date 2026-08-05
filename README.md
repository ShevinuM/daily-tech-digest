# Daily Tech Digest

A ~30-minute tech reading digest, assembled every morning and published to
Telegraph + Instapaper. Runs entirely on one Mac. No SaaS, no API keys to
paste, no task quota.

## Why this exists

The first version routed every feed fetch through Code by Zapier, because the
agent's built-in web fetch returns a stale cache for dev.to and Medium. That
worked, but Zapier bills per step and kills any code step at **one second** —
so slow feeds timed out, got retried, and each retry billed again.

It burned **100 tasks in five days** and stopped mid-run. Measured breakdown of
a single run:

| Source | Tasks | Digest items produced |
|---|---:|---:|
| dev.to fetches | 7 | 6 |
| Medium fetches | 6 | **0** |
| Article body retries | 4 | 0 |
| Telegraph publish | 2 | 0 |
| Pragmatic Engineer | 1 | 0 |
| **Total** | **20** | **6** |

Eleven of the twenty failed and still counted. Medium had contributed zero
items across every run to date — its posts are member-only and the no-paywall
rule drops them — while being the single largest line item.

Stdlib-only Python replaced all of it. Current cost: nothing.

## Architecture

```
launchd 07:10 ──► main.py fetch ──► digest_feed.json
                       │
                       └─ feeds/*.py, auto-discovered

launchd 07:30 ──► claude -p ────────┤ reads the JSON
                                    ├ reads reading rules from Notion (MCP)
                                    ├ reads newsletters from AgentMail (MCP)
                                    ├ ranks, dedupes, writes summaries
                                    └ main.py publish
                                           ├ Telegraph  (no auth)
                                           └ Instapaper (Keychain)
```

Deterministic work — fetching, filtering, converting, publishing — is plain
Python. Judgement — what's worth reading, how to summarise it — is the model.
Splitting them means the fetch is debuggable on its own and a bad morning shows
up in a log rather than as a mysteriously thin digest.

## Layout

```
main.py                  entry point: fetch / publish / feeds
utils.py                 http, RSS parsing, dates, paywall detection, item shape
publish.py               HTML -> Telegraph nodes -> published, then Instapaper
feeds/                   one module per source, auto-discovered
  __init__.py            discovery
  dev_to.py
  medium.py
  pragmatic_engineer.py
  hacker_news.py
config.json              account identifiers — GITIGNORED, never committed
config.example.json      placeholders, tracked
scripts/check-secrets.sh pre-push gate; also usable as a pre-commit hook
tests/test_offline.py    68 tests, no network
launchd/                 plist template, __HOME__ substituted at install
ROUTINE_PROMPT.md        the routine's prompt; installed as SKILL.md
```

## Adding or removing a source

Sources are plug-ins. Drop a file in `feeds/` to add one; delete it to remove
one. Nothing else needs editing — not `main.py`, not the routine prompt, not
the tests.

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

Raising inside `fetch` is fine — `main.py` records the error and carries on
with the other feeds, so one dead source never costs you a digest.

## Install

```bash
./install.sh
```

Checks prerequisites, runs the tests, copies `main.py`, `utils.py`,
`publish.py`, `config.json` and the whole of `feeds/` to `~/Claude/digest`,
installs the routine prompt, and loads the launchd job. It refuses to install
if the tests fail, and mirrors `feeds/` exactly so a deleted source also
disappears from the installed copy.

Then, once, add the Instapaper credential yourself — the bare `-w` makes macOS
prompt interactively so it never reaches your shell history:

```bash
security add-generic-password -s digest-instapaper -a your@email.com -w
```

Telegraph needs nothing; `publish.py` creates an anonymous account on first run
and caches the token at `~/.config/digest/telegraph.json` (chmod 600).

## Usage

```bash
python3 main.py feeds
python3 main.py fetch --verbose
python3 main.py fetch --only dev_to hacker_news
python3 main.py publish --html digest.html --title "Tech Reading Digest" --dry-run
python3 main.py publish --html digest.html --title "Tech Reading Digest"
```

### `fetch`

| Flag | Does |
|---|---|
| `--out PATH` | Output path (default `digest_feed.json`) |
| `--hours N` | Freshness window (default from `config.json`) |
| `--only FEED…` | Run only these feeds |
| `--no-bodies` | Skip article body fetch — faster, thinner summaries |
| `--verbose` | Per-source counts and cutoff, to stderr |

Exit: `0` got items · `1` ran but empty · `2` couldn't write.

### `publish`

| Flag | Does |
|---|---|
| `--html PATH` | Digest HTML fragment (required) |
| `--title` | Page title (required) |
| `--dry-run` | Print converted nodes, publish nothing |
| `--no-instapaper` | Telegraph only |
| `--author` / `--account` | Override the values in `config.json` |

Exit: `0` published · `1` Telegraph failed · `2` published but Instapaper
failed · `3` bad input.

Telegraph accepts a fixed tag set, so the converter remaps rather than failing:
`h1`/`h2`→`h3`, `h5`/`h6`→`h4`, `div`→`p`, `del`→`s`, `span` unwrapped,
`script`/`style` dropped. Only `href` and `src` survive. Anything remapped is
reported on stderr. Content is capped at 64 KB — a 9-item digest is ~13 KB.

## Output format

```json
{
  "generated_at": "2026-08-05T07:10:04Z",
  "cutoff": "2026-08-04T07:10:04Z",
  "feeds": ["dev_to", "hacker_news", "medium", "pragmatic_engineer"],
  "counts": { "dev_to": 44, "hacker_news": 14, "medium": 13, "pragmatic_engineer": 0 },
  "total_items": 71, "usable_items": 58, "errors": [],
  "items": [{
    "source": "dev.to", "title": "...", "url": "...",
    "published_at": "2026-08-04T09:13:44Z", "author": "...",
    "tags": ["architecture"], "description": "...",
    "paywalled": false, "body_excerpt": "first ~2500 chars",
    "reactions": 12, "reading_minutes": 11
  }]
}
```

Every item carries the fields in `utils.ITEM_FIELDS`; feeds may add their own
ranking signals on top (`reactions` for dev.to, `score` and `discussion_url`
for Hacker News).

- `paywalled` is `true` for **every** Medium item — RSS genuinely cannot tell
  member-only stories apart, so they're flagged rather than guessed at. Hacker
  News items are flagged by domain (WSJ, FT, NYT, Bloomberg…).
- `body_excerpt` is filled for the top 25 dev.to items and all Pragmatic
  Engineer items. Medium and HN feeds don't carry bodies.

## Configuration

`config.json` holds account identifiers and is **gitignored** — this repo is
public. Copy `config.example.json` and fill it in; `install.sh` does this for
you on first run and refuses to proceed with placeholder values.

Per-feed tunables live in the feed module itself: `TAGS` in `dev_to.py` and
`medium.py`, `MIN_SCORE` and `SCAN` in `hacker_news.py`, `PAYWALL_HINTS` in
`utils.py`.

What actually goes in the digest is **not** configured here — it lives in a
plain-English Notion page the routine reads every morning, so changing your
reading interests doesn't require touching code.

## Tests

```bash
python3 tests/test_offline.py     # 68/68, no network
```

Covers utils, all four feed modules, plug-in discovery (including that adding
and deleting a file changes the active source list), the Telegraph converter,
and the publisher's error paths. Two real bugs were caught this way:
`strip_html` collapsed whitespace before stripping tags, doubling spaces in
every Medium snippet and Pragmatic Engineer body.

**Not covered:** live HTTP. After changing anything that makes a request, run
`main.py fetch --verbose` and `main.py publish --dry-run` by hand.

## Secrets

`scripts/check-secrets.sh` fails if `config.json` is tracked, if any literal
value from it appears in a tracked file, if a generated artefact is staged, or
if a common credential pattern matches. Run it before pushing, or install it as
a hook:

```bash
ln -sf ../../scripts/check-secrets.sh .git/hooks/pre-commit
```

It has already caught one real leak — an email address that had been pasted
into a setup document.

## Known limitations

- **macOS only.** Uses `security` and `launchctl`.
- **Runs only when the Mac is awake.** Asleep at 07:30 means that day is
  skipped, not delayed. That's the cost of dropping the cloud scheduler.
- **Medium contributes nothing in practice.** Kept because it's free locally
  and the paywall flag is honest, but nothing survives the no-paywall rule.
- **Newsletter items need date verification.** TLDR and The Rundown routinely
  surface stories 1–2 days old; arrival date is not publish date. The routine
  prompt requires verifying each one and dropping anything undatable.
