# Architecture

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

Deterministic work — fetching, filtering, deduping, date-verifying,
per-source thresholds, topic-relevance scoring, and extractive
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

## Repo layout

```
main.py                  CLI: feeds / fetch / digest / pools / delete-threads
utils.py                 http, RSS/Atom parsing, dates, item shape
feeds/                   one module per source, auto-discovered
  discovery:  hacker_news.py  bytebytego.py  gary_marcus.py
              ed_zitron.py
              dev_to.py  medium.py        (PAUSED — ENABLED = False)
  priority:   pragmatic_engineer.py  jason_wei.py  ken_walger.py
              alperen_keles.py  martin_fowler.py
newsletters/             AgentMail REST client, classification, unsubscribe
rank/
  pools.py               pool 1 -> pool 2: per-source thresholds/caps
  enrich.py               fetch + extract article body text, markdown/URL scrub
  relevance.py            pool 2 -> pool 3: model2vec scoring vs interests.md
  summarize.py            sumy TextRank extractive summaries
  prompt.py               the one LLM prompt template
  llm_client.py           Gemini -> Groq -> OpenRouter fallback, plain urllib
  merge.py                cutoff/dedupe assembly (used by pools.py)
  write_site_content.py   site-content + reading-hub JSON writer
site/                    Astro site (content collection `digests`)
reading-hub/             git submodule -> private daily-tech-digest-hub repo
config.json               non-secret tunables, incl. pools/relevance/summarize
scripts/check-secrets.sh pre-push gate; also usable as a pre-commit hook
tests/test_offline.py    offline tests, no network
.github/workflows/digest.yml
```

The Astro front end has its own notes in [`site/README.md`](../site/README.md).
