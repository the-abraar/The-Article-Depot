# The Article Depot

An unattended pipeline that reads Google Trends, writes explainers grounded in
the reporting behind each trend, checks them before publishing, and prunes
them when the topic goes cold.

```
Trends RSS ──▶ polite fetch ──▶ grounding sources ──▶ Claude (draft)
                                                          │
                          rejected ◀── quality gate ◀──────┘
                                             │ passed
                                             ▼
                          static site ◀── publish ──▶ prune (stale)
```

Everything is driven by one SQLite file (`depot.db`). The site is a pure
function of that database: re-running `depot publish` always reproduces
exactly the right pages, and nothing else.

## Quick start

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
export ANTHROPIC_API_KEY=sk-ant-...
export DEPOT_CONTACT_EMAIL=you@example.com

.venv/bin/depot run            # one full cycle
.venv/bin/depot status         # what has been published
.venv/bin/depot prune --dry-run
.venv/bin/depot publish        # rebuild the site from the database
```

Runs daily in CI via `.github/workflows/daily.yml`.

## The two hard questions

Anything that publishes model output with nobody watching invites the same
two questions. The answers are design decisions, not disclaimers.

### "What stops it getting blocked?"

`pipeline/fetcher.py`:

- **robots.txt is fetched once per host and obeyed**, including `Crawl-delay`.
  Reading robots.txt and then ignoring it is a worse signal than never reading
  it at all, so `Crawl-delay` can only raise our delay, never lower it below
  the configured floor.
- **Requests are spaced by a per-host clock**, so the delay is enforced rather
  than assumed.
- **A real, contactable User-Agent** — `ArticleDepotBot/1.0 (+repo URL)`.
- **429/403 means back off, not retry harder**: exponential backoff honouring
  `Retry-After`, and after three consecutive blocks the host is dropped for
  the rest of the run. A 404 is final and never retried.

### "What stops it publishing a hallucination?"

Two independent mechanisms, because either alone is weak.

**Grounding, enforced by the output schema.** The model gets the fetched
source text and nothing else, and `Draft` requires every factual claim to
carry the index of the source supporting it. A claim that cannot be attributed
cannot be represented in the output. If fewer than `min_sources` sources
survive fetching, the trend is skipped rather than written from the model's
own recall.

**A gate that can veto**, in `pipeline/quality.py`, cheapest layer first:

| Layer | Catches | Cost |
|---|---|---|
| Structural | length, shape, placeholder text, claims citing a source that was never fetched | free |
| Duplicate | the same trend resurfacing and producing the same article twice | free |
| Grounding audit | a fluent, well-shaped hallucination | one API call |

The audit is a second Claude call that reads the draft *against the sources*
and returns every claim they do not support. A draft publishes only if all
three layers pass; anything else is stored with its reason and never reaches
the site. The cheap layers run first, so a structurally broken draft costs
nothing to reject.

## "Auto-prunes stale content" — the actual trigger

Stale is not self-defining, so it is a conjunction (`pipeline/prune.py`):

> the article's trend has not appeared in the Trends feed for
> **14 days** *and* the article is at least **30 days** old

Both are required, and each blocks a specific wrong deletion. Without the age
floor, one quiet fortnight would take a fresh article down. Without the
trend-silence condition, a still-trending topic would be pruned purely for
being old. Recency is not staleness; *nobody searching for it any more* is.

Pruning sets `pruned_at` rather than deleting, so it is reversible — the next
publish simply stops emitting the page.

## Running twice does nothing twice

Claiming work is a `UNIQUE(trend_slug, run_date)` insert. A second run on the
same day gets `None` back and skips, so a double-fired cron, a manual re-run,
or a retried CI job cannot produce a duplicate article. The scheduled workflow
also holds a `concurrency` group so a slow run never overlaps the next firing.

A run that dies halfway leaves rows in a non-terminal state; `store.resumable()`
is what the next run picks up, rather than starting over.

## Layout

| Path | What it does |
|---|---|
| `pipeline/config.py` | every tunable, read from the environment |
| `pipeline/store.py` | SQLite state, idempotency, provenance |
| `pipeline/fetcher.py` | robots.txt, crawl-delay, rate limiting, backoff |
| `pipeline/trends.py` | Trends RSS → normalised trends |
| `pipeline/sources.py` | news links → grounding text |
| `pipeline/generate.py` | Claude call, grounded structured output |
| `pipeline/quality.py` | the three-layer pre-publish gate |
| `pipeline/publish.py` | static site rendering |
| `pipeline/prune.py` | the staleness trigger |
| `pipeline/run.py` | stage orchestration |

## Configuration

All via environment variables; defaults in `pipeline/config.py`.

| Variable | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | required to generate |
| `DEPOT_GEO` | `US` | Trends region |
| `DEPOT_MAX_TRENDS` | `5` | trends per run |
| `DEPOT_MIN_SOURCES` | `2` | below this a trend is skipped, not written |
| `DEPOT_MODEL` | `claude-opus-5` | writer |
| `DEPOT_AUDITOR_MODEL` | `claude-opus-5` | fact-checker |
| `DEPOT_MIN_AUDIT_SCORE` | `4` | of 5, on grounding and clarity |
| `DEPOT_STALE_TREND_DAYS` | `14` | half the prune trigger |
| `DEPOT_PRUNE_MIN_AGE_DAYS` | `30` | the other half |
| `DEPOT_CRAWL_DELAY` | `2.0` | per-host floor, seconds |
| `DEPOT_CONTACT_EMAIL` | `corrections@example.com` | shown on the contact page |

## Tests

```bash
.venv/bin/python -m pytest -q
```

57 tests, no network and no API calls — the fetcher takes an injected URL map
and the client takes scripted structured outputs, so every guardrail is driven
into its failure mode deliberately: robots disallow, `Crawl-delay` honoured and
never lowered, `Retry-After`, host dropped after repeated 403s, claims citing a
source that does not exist, a fluent hallucination that scores well on clarity,
both halves of the prune trigger at their boundaries, and a second same-day run
proving it is a no-op.

## Deploying

The workflow deploys to GitHub Pages. Set repository **Settings → Pages →
Source** to **GitHub Actions**, and add `ANTHROPIC_API_KEY` under **Settings →
Secrets and variables → Actions**. `DEPOT_CONTACT_EMAIL` belongs in *Variables*,
not Secrets.

## Honest limits

- Articles are machine-written. They are grounded and automatically checked,
  but no human reads every one before it publishes; the About page says so.
- Extraction is a dependency-free heuristic stripper, not a full readability
  implementation. Unusual page structures yield thinner grounding, which shows
  up as a skipped trend rather than a worse article.
- Search engines treat scaled automated content as spam when it exists to game
  rankings rather than help readers. Grounding, citation and pruning are what
  keep this on the right side of that line — worth knowing before scaling the
  volume up.
