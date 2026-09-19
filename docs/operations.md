# Operations

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
