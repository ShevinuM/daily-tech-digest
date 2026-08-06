## !!Still In Progress

This is a prototype. I'm working on backend + frontend rewrite for this. And I'm working on new features listed on Issues.

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
  4. rank/pools.py          pool 1 (raw) -> pool 2: per-source thresholds/caps
  5. rank/enrich.py         fetch + extract article text for items with no
                             description, so relevance scoring is comparable
                             across sources
  6. rank/relevance.py      pool 2 -> pool 3 (top ~25): model2vec embeddings
                             vs reading-hub/interests.md, non-tech drop,
                             per-source caps — no LLM call
  7. rank/summarize.py      sumy TextRank extractive summary per pool-3 item
  8. rank/prompt.py + llm_client.py   ONE batched LLM call (Gemini, falling
                             back to Groq/OpenRouter): pick, group, and write
                             prose for the pre-scored, pre-summarized items
  9. write site/src/content/digests/<date>.json
  10. update reading-hub/newsletters.json + reading-hub/reading-pace.json
  11. commit + push both repos
  12. astro build -> deploy to GitHub Pages
```

Deterministic work — fetching, filtering, deduping, date-verifying, paywall
detection, per-source thresholds, topic-relevance scoring, and extractive
summarization — is plain Python (stdlib plus model2vec/sumy/trafilatura, no
LLM calls). What's left for the model is genuine judgement: which of the
~25 pre-scored candidates to keep, how to group them, and rewriting each
extractive summary into prose — one batched call per run (~6-7k tokens, down
from ~35k when an LLM did the filtering too), using Google AI Studio's free
tier, falling back to Groq/OpenRouter if it's down.

The reading hub — your topics, priorities, "dial up/down" list, newsletter
registry, and reading-pace log — lives in a **separate private repo**
(`daily-tech-digest-hub`), linked here as a git submodule at `reading-hub/`.
That keeps personal reading habits and email addresses private while the
digest *output* stays public.

## Layout

```
main.py                  CLI: feeds / fetch / digest / pools / delete-threads
utils.py                 http, RSS parsing, dates, paywall detection, item shape
feeds/                   one module per source, auto-discovered
  dev_to.py  medium.py  pragmatic_engineer.py  hacker_news.py
newsletters/             AgentMail REST client, classification, unsubscribe
rank/
  pools.py               pool 1 -> pool 2: per-source thresholds/caps
  enrich.py               fetch + extract article body text, markdown/URL scrub
  relevance.py            pool 2 -> pool 3: model2vec scoring vs interests.md
  summarize.py            sumy TextRank extractive summaries
  prompt.py               the one LLM prompt template
  llm_client.py           Gemini -> Groq -> OpenRouter fallback, plain urllib
  merge.py                cutoff/dedupe/paywall assembly (used by pools.py)
  write_site_content.py   site-content + reading-hub JSON writer
site/                    Astro site (content collection `digests`)
reading-hub/             git submodule -> private daily-tech-digest-hub repo
config.json               non-secret tunables, incl. pools/relevance/summarize
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

`rank/relevance.py` (model2vec) needs Python **3.12+** — pip silently
back-solves to an older, incompatible version on 3.9. Set up a dedicated venv
once:

```bash
brew install python@3.12
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Every command below assumes `.venv/bin/python`/`.venv/bin/python3`. CI
already uses 3.12.

```bash
.venv/bin/python tests/test_offline.py          # offline, no network
.venv/bin/python main.py feeds
.venv/bin/python main.py fetch --verbose        # real network
.venv/bin/python main.py pools --verbose        # fetch -> pool2 -> enrich -> relevance,
                                                 # no LLM call, no writes — the tool for
                                                 # tuning relevance.weights in config.json
.venv/bin/python main.py digest --dry-run       # full pipeline, writes locally, doesn't push/delete
cd site && npm install && npm run build
./scripts/check-secrets.sh                      # must exit 0 before any push
```

`main.py digest` needs `reading-hub/` checked out (`git submodule update
--init`) and reads `GEMINI_API_KEY` from the environment (required — or
`GROQ_API_KEY`/`OPENROUTER_API_KEY` as a fallback) and
`AGENTMAIL_API_KEY`/`AGENTMAIL_INBOX` (optional — without them it degrades
to feed-only, same as a source returning nothing). The embedding model
(~125 MB) and nltk's sentence-tokenizer data download on first use, cached
under `~/.cache/huggingface` and `~/nltk_data`.

## Secrets and variables (GitHub Actions)

Set these under the repo's Settings → Secrets and variables → Actions:

| Name | What |
|---|---|
| `GEMINI_API_KEY` | Google AI Studio API key (free tier) |
| `GROQ_API_KEY` | optional — fallback if Gemini is down |
| `OPENROUTER_API_KEY` | optional — fallback if both of the above are down |
| `AGENTMAIL_API_KEY` | AgentMail REST API key |
| `AGENTMAIL_INBOX` | the AgentMail inbox address newsletters arrive at |
| `HUB_REPO_TOKEN` | fine-grained PAT, Contents Read+Write on **both** this repo and the private hub repo — `GITHUB_TOKEN` can't check out or push to a separate private repo |

Non-secret tunables (`target_read_minutes`, `freshness_hours`, LLM model
names, site title, and the `pools`/`relevance`/`summarize` blocks) live in
the tracked `config.json` — no secrets are stored there, so there's nothing
to keep out of the public repo.

Also required once, by hand:
- Settings → Pages → Source → **GitHub Actions**.
- Uncomment the `schedule:` trigger in `.github/workflows/digest.yml` after
  a manual `workflow_dispatch` run has been verified end to end.

## Tests

```bash
.venv/bin/python tests/test_offline.py
```

Covers utils, every feed module, plug-in discovery, newsletter
classification/date-verification/unsubscribe-link extraction, pool
assembly/caps, interests.md parsing, relevance-scoring math (via a stubbed
encoder — no model download), the prompt template, and index-keyed
reconciliation. **Deliberately offline and model-free** — nothing here
downloads model2vec weights or nltk data. **Not covered:** live HTTP or the
actual sumy/model2vec output quality — run `main.py fetch --verbose`,
`main.py pools --verbose`, and `main.py digest --dry-run` by hand after
changing anything that makes a request or touches scoring/summarization.

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
- **No paywall workaround.** A paywalled item is dropped outright rather than
  searched for a free mirror — except Medium, whose RSS feeds can't
  distinguish member-only stories from free ones at all; those are let
  through with a "member-only" badge instead of being dropped wholesale
  (see `reading-hub/interests.md`'s paywall rule and `config.json`'s
  `pools.allow_paywalled`).
- **Reading-pace calibration is semi-manual.** An unattended run can only
  log an *estimated* read time; edit `reading-hub/reading-pace.json` by hand
  whenever you want to record an actual one.
- **A failed run doesn't carry its content forward.** The freshness window
  is always `now - 24h`, not "since the last successful publish" — if a run
  fails partway (Gemini quota, both pushes rejected, etc.), that day's
  candidate items simply age out of the next run's window rather than being
  retried. AgentMail cleanup is deliberately deferred until after both
  pushes succeed (`main.py delete-threads`, a separate workflow step) so a
  failed run at least doesn't also delete its own source newsletters — but
  there's no automatic retry of a failed day's content.
