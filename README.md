# Daily Tech Digest

A daily tech reading digest: fetched, filtered, ranked, and summarized on a
schedule by GitHub Actions, published to a small [Astro](https://astro.build)
site on GitHub Pages. No server to run, no SaaS bill — a scheduled workflow,
a free-tier AI call, and a static site.

## Architecture

```
GitHub Actions (cron, .github/workflows/digest.yml)
  1. checkout, with the private reading-hub submodule
  2. main.py fetch          feeds/*.py, auto-discovered -> digest_feed.json
  3. newsletters/           AgentMail REST -> classified, date-verified items
  4. rank/                  merge + dedupe, then 2 batched Gemini calls:
                             selection (rank candidates) -> summary (write prose)
  5. write site/src/content/digests/<date>.json
  6. update reading-hub/newsletters.json + reading-hub/reading-pace.json
  7. commit + push both repos
  8. astro build -> deploy to GitHub Pages
```

Deterministic work — fetching, filtering, deduping, date-verifying, paywall
detection — is plain stdlib Python. Judgement — what's worth reading, how to
summarize it — goes to a small number of batched Gemini calls per run (not
one call per item), using Google AI Studio's free tier.

The reading hub — your topics, priorities, "dial up/down" list, newsletter
registry, and reading-pace log — lives in a **separate private repo**
(`daily-tech-digest-hub`), linked here as a git submodule at `reading-hub/`.
That keeps personal reading habits and email addresses private while the
digest *output* stays public.

## Layout

```
main.py                  CLI: feeds / fetch / digest
utils.py                 http, RSS parsing, dates, paywall detection, item shape
feeds/                   one module per source, auto-discovered
  dev_to.py  medium.py  pragmatic_engineer.py  hacker_news.py
newsletters/             AgentMail REST client, classification, unsubscribe
rank/                    merge/dedupe, Gemini prompts + client, site-content writer
site/                    Astro site (content collection `digests`)
reading-hub/             git submodule -> private daily-tech-digest-hub repo
config.json               non-secret tunables (tracked — see Secrets below)
scripts/check-secrets.sh pre-push gate; also usable as a pre-commit hook
tests/test_offline.py    offline tests, no network
.github/workflows/digest.yml
```

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

Raising inside `fetch` is fine — it's recorded as an error and the run
continues with the other feeds.

## Local development

```bash
python3 tests/test_offline.py          # offline, no network
python3 main.py feeds
python3 main.py fetch --verbose        # real network
python3 main.py digest --dry-run       # full pipeline, writes locally, doesn't push/delete
cd site && npm install && npm run build
./scripts/check-secrets.sh             # must exit 0 before any push
```

`main.py digest` needs `reading-hub/` checked out (`git submodule update
--init`) and reads `GEMINI_API_KEY` from the environment (required) and
`AGENTMAIL_API_KEY`/`AGENTMAIL_INBOX` (optional — without them it degrades
to feed-only, same as a source returning nothing).

## Secrets and variables (GitHub Actions)

Set these under the repo's Settings → Secrets and variables → Actions:

| Name | What |
|---|---|
| `GEMINI_API_KEY` | Google AI Studio API key (free tier) |
| `AGENTMAIL_API_KEY` | AgentMail REST API key |
| `AGENTMAIL_INBOX` | the AgentMail inbox address newsletters arrive at |
| `HUB_REPO_TOKEN` | fine-grained PAT, Contents Read+Write on **both** this repo and the private hub repo — `GITHUB_TOKEN` can't check out or push to a separate private repo |

Non-secret tunables (`target_read_minutes`, `freshness_hours`, Gemini model
name, site title) live in the tracked `config.json` — no secrets are stored
there, so there's nothing to keep out of the public repo.

Also required once, by hand:
- Settings → Pages → Source → **GitHub Actions**.
- Uncomment the `schedule:` trigger in `.github/workflows/digest.yml` after
  a manual `workflow_dispatch` run has been verified end to end.

## Tests

```bash
python3 tests/test_offline.py
```

Covers utils, every feed module, plug-in discovery, newsletter
classification/date-verification/unsubscribe-link extraction, rank
merge/dedup/prompt construction, and the reading-pace target-count
calibration. **Not covered:** live HTTP — run `main.py fetch --verbose` and
`main.py digest --dry-run` by hand after changing anything that makes a
request.

## Secrets scanning

`scripts/check-secrets.sh` fails if a generated artefact or an
instruction/plan `.md` file is tracked, or a common credential pattern
matches anywhere in a tracked file. Run it before pushing, or install it as
a hook:

```bash
ln -sf ../../scripts/check-secrets.sh .git/hooks/pre-commit
```

## Known limitations

- **Newsletter item dates are verified, not trusted.** Newsletters routinely
  resurface 1-2 day old stories; an item is only kept if its original
  publish date can be established from its URL (a `/YYYY/MM/DD/` path, or an
  X/Twitter snowflake ID) — otherwise it's dropped rather than guessed at.
- **No paywall workaround.** A paywalled item is dropped outright rather
  than searched for a free mirror.
- **Reading-pace calibration is semi-manual.** An unattended run can only
  log an *estimated* read time; edit `reading-hub/reading-pace.json` by hand
  whenever you want to record an actual one.
