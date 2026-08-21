# Handoff — The Article Depot

*Written 2026-08-21. Commit `cc72cc7`, pushed to `main`.*

## Where this stood before

Five static HTML pages and a `console.log`, all committed on 2 May 2023 and
untouched since. The four "articles" were hardcoded placeholder blurbs about
yoga and sleep hygiene, every link pointed at `#`, and there was no scraper,
no LLM call, no publishing step and no pruning anywhere in the repo.

The CV described it as: *"Built fully automated pipeline that scrapes Google
Trends, generates articles via LLM, publishes, and auto-prunes old content;
running production system."* The repo is public, so that gap was visible to
anyone who looked.

## What exists now

```
Trends RSS ──▶ polite fetch ──▶ grounding sources ──▶ Claude (draft)
                                                          │
                          rejected ◀── quality gate ◀──────┘
                                             │ passed
                                             ▼
                          static site ◀── publish ──▶ prune (stale)
```

Ten modules under `pipeline/`, all state in one SQLite file (`depot.db`),
scheduled daily by `.github/workflows/daily.yml`. The site is a pure function
of the database — `depot publish` reproduces exactly the right pages and
deletes orphans, which is how a prune reaches the public site.

| Path | Role |
|---|---|
| `pipeline/config.py` | every tunable, from the environment |
| `pipeline/store.py` | SQLite state, idempotency, provenance |
| `pipeline/fetcher.py` | robots.txt, crawl-delay, rate limiting, backoff |
| `pipeline/trends.py` | Trends RSS → normalised trends |
| `pipeline/sources.py` | news links → grounding text |
| `pipeline/generate.py` | Claude call, grounded structured output |
| `pipeline/quality.py` | the three-layer pre-publish gate |
| `pipeline/publish.py` | static site rendering |
| `pipeline/prune.py` | the staleness trigger |
| `pipeline/run.py` | stage orchestration |

The three design decisions that carry the weight:

1. **Grounding is enforced by the output schema**, not by a prompt
   instruction. `Draft` requires every factual claim to carry the index of
   the source supporting it, so an unattributable claim cannot be
   represented. Below `min_sources` usable sources the trend is skipped
   rather than written from the model's own recall.
2. **The gate has three layers, any of which vetoes** — structural, duplicate,
   then a second Claude call reading the draft *against* the sources. Cheap
   layers run first, so a structurally broken draft costs no API spend.
3. **Pruning has a stated trigger** — trend absent from the feed 14 days AND
   article at least 30 days old. Both required. It sets `pruned_at` rather
   than deleting, so it is reversible.

## What is actually verified

- **57 tests pass**, with no network and no API calls. The fetcher takes an
  injected URL map, the client takes scripted structured outputs, so each
  guardrail is driven into its failure mode deliberately.
- **Live run against the real Google Trends feed**: ingestion, polite
  fetching, extraction and the guardrails all ran end to end on real sites.
  The 403 backoff and host-drop fired unprompted on `denvergazette.com`
  (two backoffs, then dropped). Generation failed closed on the absent API
  key, was contained per-trend, and the run still completed and rebuilt
  the site.
- **Not yet verified: the two Claude calls.** No API key was available, so
  generation and the grounding audit have never run against the real model.
  Their shapes are tested against the SDK, but the first real run is the
  first time a genuine draft passes through the gate.

## Do these two things before it can work

The workflow is **paused** — disabled in the Actions tab on 2026-08-21,
pending API credits, so nothing fires and no failure mail arrives. Do both of
the following before re-enabling it; without them a run publishes nothing and
fails at the deploy step.

Re-enable with:

```bash
gh workflow enable "Daily publish" --repo the-abraar/The-Article-Depot
```

1. **Add `ANTHROPIC_API_KEY`** under Settings → Secrets and variables →
   Actions. Without it every trend is skipped.
2. **Set Pages → Source to "GitHub Actions."** The old root `index.html` is
   gone, so Pages serving from root will 404.

Optional but worth it: set `DEPOT_CONTACT_EMAIL` as a repository *Variable*
(not a secret). The contact page renders a `{{contact_email}}` placeholder
until you do — a real address was deliberately not committed.

## What to do next, in order

**1. One supervised run before trusting it unattended.**
Run `depot -v run --limit 1` locally with a key and read the article it
produces. That is the first real exercise of both model calls. Check the
claims against the cited sources yourself once — the whole design rests on
the auditor being strict, and you want to see it work before it runs alone.

**2. Improve source extraction — the weakest link.**
`pipeline/sources.py` is a dependency-free heuristic stripper. On the live
run the Yahoo page yielded good prose *plus* a tail of unrelated article
teasers (Garth Brooks, Prince Harry) inside the grounding text. The auditor
is a backstop, but noise in the grounding is the most likely cause of a bad
article getting through. A proper readability pass (largest contiguous
text-dense block, or `trafilatura` if you accept the dependency) would
tighten this materially. This is the highest-value change available.

**3. Tune the gate on real output.**
`min_audit_score` is 4/5 on both grounding and clarity, and any unsupported
claim is an automatic reject. That is deliberately strict. After a week of
real runs, check `depot status` and the `reason` column — if good drafts are
being rejected, loosen it knowingly rather than by accident.

**4. Then consider volume.**
`DEPOT_MAX_TRENDS` is 5. Do not raise it before steps 1–3. Search engines
treat scaled automated content as spam when it exists to game rankings;
grounding, citation and pruning are what keep this on the right side of that
line, and they should be proven before the volume goes up.

## Known limits

- Extraction is heuristic (see step 2).
- Articles are machine-written. They are grounded and automatically checked,
  but no human reads every one before it publishes. The About page says so
  plainly rather than implying a newsroom.
- `depot.db` is committed by CI on every run. That is deliberate — it holds
  the idempotency keys and the trend last-seen dates the prune trigger reads,
  and losing it would republish everything. It will accumulate commits.
- The Trends RSS feed is a published interface but not a contractual one. If
  its shape changes, `pipeline/trends.py` is the single place to fix.

## For the CV and the interview

The bullet is now substantially true. Two notes:

- *"implemented without traditional coding through Vibe Coding techniques"*
  is the one phrase worth rewording. The defensible story here is the
  guardrail design — schema-enforced grounding, a gate that can veto, a
  named staleness trigger, idempotent claiming — and that phrasing works
  against you by inviting a question about rigour instead.
- `prep/lessons/0005-article-depot-unattended-pipeline.md` asks three "in
  your words" questions. All three now have concrete answers in this repo:
  the crawler manners are in `fetcher.py`, the quality gate is `quality.py`,
  and the staleness trigger is the docstring at the top of `prune.py`.
