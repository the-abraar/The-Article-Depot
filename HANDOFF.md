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

## 2026-10-15: main-content extraction (step 2)

**What changed**

- `pipeline/sources.py`: `extract_text(raw_html, max_chars)` keeps its
  signature, but now picks the article first. It parses the page with stdlib
  `html.parser` and runs a readability-style score: each prose `<p>` votes for
  its parent and grandparent, and the vote is discounted by link density. Only
  the winning container is kept, plus a sibling that scores at least half as
  high (for a body split in two divs). Inside it, link lists ("Read more: ...")
  and lines of 40 characters or less are dropped. Nav, header, footer, aside,
  forms, figures and elements whose class/id says related, promo, sidebar,
  newsletter, trending and the like are skipped. If that yields under 200
  characters (no `<p>` markup), it falls back to the old stripper
  (`_strip_text`, plus the `_main_block` tail drop), which now also removes
  HTML comments. Very deep tag soup that raises `RecursionError` takes the
  fallback too.
- No new dependency. `trafilatura` was not used, because stdlib keeps the
  install surface to the Anthropic SDK and the fixtures pass without it.
- `pipeline/publish.py`: the nav f-string had a backslash inside an
  expression, which is a syntax error on Python 3.11 even though
  `requires-python` is `>=3.11`. Fixed in its own commit.
- `tests/fixtures/*.html`: three synthetic pages, no copied content. One is a
  portal story with a "More from" teaser rail, an in-body "Read more" list, a
  trending sidebar and nav. One has a body split across sibling divs with a
  newsletter promo between the halves. One has no `<p>` markup. On these
  fixtures the old stripper leaked the promo, the rail links and the
  "Read more" links.

**How verified**

- `python -m pytest -q` on Python 3.11 gives 63 passed (60 before, plus 3
  fixture tests). No network, no API calls.
- A synthetic 533 KB page extracts in about 0.07 s. A page nested 5000 levels
  deep falls back cleanly.

**What is left**

- Not checked against real news pages (no live fetches this session). On the
  next supervised run (step 1), read the `sources` text for each trend, the
  Yahoo-style pages especially, and save a trimmed, synthetic copy of any
  page that fails as a new fixture.
- Known gap (marked `shortcut:` in the code): a body split across two sibling
  divs is merged by score. Other layouts, such as a body split across many
  scattered containers, rely on the grandparent vote or fall to the auditor.
- Steps 1, 3 and 4 above are unchanged. The workflow is still paused.

## 2026-10-22: extractor checked against live news pages

**What was run**

Fetch only, no API key, nothing published. One request for the US Trends
feed (10 trends, 30 news links), then one page each from 8 distinct hosts
in it, Yahoo first, through `PoliteFetcher` (robots.txt, 2 s crawl delay,
the bot User-Agent). Pages stayed in a scratch directory, not the repo.

| # | Page (publisher, kind) | Extracted | Verdict |
|---|---|---|---|
| 0 | Yahoo Sports, story | 4560 chars | Clean: body only, no teasers |
| 1 | Yahoo Actualités (fr), story | 2071 | Clean |
| 2 | Yahoo, video page | 351 | **Bad**: an unrelated teaser card ("Many books will provide...") instead of the clip's caption |
| 3 | Yahoo Malaysia (Deadline syndication), photo story | 2365 | Clean, plus the syndicator's one-line newsletter plug |
| 4 | Pulse Sports Nigeria, story | 3110 | Clean, plus an image credit and an embedded tweet |
| 5 | Yardbarker, story | 1040 | Clean |
| 6 | Eurosport (fr), live commentary | 1773 | Usable. No `<p>` prose, so the stripper ran; one nav line and a repeated headline leak |
| 7 | Le Figaro (fr), live blog | 804 | Under-extracted: one live post (the line-ups) of about 12,400 chars of prose spread over 99 post containers |

The Garth Brooks style teaser tail from the first live run did not show up
on any story page.

**What changed**

- `pipeline/sources.py`, page 2: Yahoo teaser cards are
  `<article><a><div><p>`. `_blocks` only counted link text when the walk
  started *at* an `<a>`, so a paragraph or container sitting inside a link
  scored as plain prose and won. It now checks the node's ancestors
  (`_in_link`). That left only the 172-char caption, and the `< 200` floor
  then sent the page to the stripper, which returned the whole teaser rail
  (4419 chars). The stripper now runs only when the readability pass finds
  no prose at all; a short pick stays short and `gather()` drops the page as
  a stub, which is the right call for a video page. Pages 0, 1 and 3 to 7
  extract byte-for-byte the same as before.
- `tests/fixtures/video_teaser_cards.html` (synthetic) and
  `test_linked_teaser_cards_do_not_stand_in_for_a_short_page`. It fails on
  the previous extractor.

**How verified**

- `python -m pytest -q` on Python 3.11: 64 passed. No network in tests.

**What is left**

- Live blogs (page 7) are the "many scattered containers" gap noted above:
  the winner is one post. That loses grounding, it does not add noise. Not
  fixed: it needs a merge rule for many similar sibling posts, plus a
  fixture.
- Page 6 shows the stripper still keeps a nav line longer than 40 chars.
- Trade-off of the fallback change: a page whose body is in `<div>`s but
  that has one stray prose `<p>` (a cookie notice, say) now yields that
  `<p>` and is dropped as a stub instead of being stripped. It fails toward
  fewer sources, not noisier ones.
- The workflow is still paused.
