# Shevinu's Digest

A daily digest of tech reading — fetched, filtered, ranked, and summarized on
a schedule, then published as a static site. No server, no SaaS bill: a cron'd
GitHub Actions workflow, one free-tier LLM call, and GitHub Pages.

Live at **[digest.shevinum.dev](https://digest.shevinum.dev)**.

> **Prototype.** A backend + frontend rewrite is in progress, and further
> features are tracked in [Issues](https://github.com/ShevinuM/daily-tech-digest/issues).

## How it works

`.github/workflows/digest.yml` runs daily at 02:00 UTC: fetch every source in
`feeds/` plus newsletters from AgentMail, cut the pool down with per-source
thresholds, score what's left against `reading-hub/interests.md` using
model2vec embeddings, summarize each survivor with TextRank — then make **one**
batched LLM call to pick, group, and write prose. The result is committed as
`site/src/content/digests/<date>.json` and deployed to Pages.

Everything deterministic is plain Python. What's left for the model is genuine
judgement, which is why a run costs ~6-7k tokens instead of ~35k.

Your topics, newsletter registry, and reading-pace log live in a **separate
private repo** (`daily-tech-digest-hub`), wired in as a submodule at
`reading-hub/`. Reading habits and email addresses stay private; the digest
output stays public.

Full pipeline and repo layout: [`docs/architecture.md`](docs/architecture.md).

## Stack

Python 3.12 (stdlib + model2vec, sumy, trafilatura), [Astro](https://astro.build)
with Tailwind v4 and Pagefind for the site, Gemini (free tier) with Groq and
OpenRouter as fallbacks, all on GitHub Actions and GitHub Pages.

## Commands

Set up once — `rank/relevance.py` needs Python **3.12+**, and pip silently
back-solves to an incompatible model2vec on 3.9:

```sh
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
git submodule update --init
```

| Command | What it does |
| --- | --- |
| `.venv/bin/python main.py feeds` | List the auto-discovered sources |
| `.venv/bin/python main.py fetch --verbose` | Fetch only — real network |
| `.venv/bin/python main.py pools --verbose` | Fetch → rank, no LLM call, no writes. The tool for tuning `relevance.weights` |
| `.venv/bin/python main.py digest --dry-run` | Full pipeline, writes locally, doesn't push or delete |
| `.venv/bin/python tests/test_offline.py` | Offline tests, no network, no model download |
| `./scripts/check-secrets.sh` | Pre-push gate — must exit 0 |
| `cd site && npm run build && npm test` | Build and smoke-test the site |

## Docs

- [Architecture](docs/architecture.md) — the pipeline stage by stage, and the repo layout
- [Sources](docs/sources.md) — adding, pausing, and pinning a feed; priority and never-drop tiers
- [Operations](docs/operations.md) — local setup, Actions secrets, tests, secret scanning, known limitations
- [`site/README.md`](site/README.md) — the Astro front end

## Licence

The site's layout and theme are ported from [AstroPaper](https://github.com/satnaing/astro-paper)
under MIT — see [`site/THEME-LICENSE`](site/THEME-LICENSE), which that port is
required to retain.
