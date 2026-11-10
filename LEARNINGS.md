# Learnings: The Article Depot

Status on 2026-11-10: built and tested offline, never launched. The daily
workflow has been disabled since 2026-08-21 and has 0 runs
(`gh run list -R the-abraar/The-Article-Depot` is empty), the site URL
returns 404, and **0 articles have been published**. Neither model call
(writer or auditor) has ever run against the real API.

## 1. The 2023 placeholder site vs the CV

Until August 2026 the repo was five static HTML pages from 2 May 2023
(`git log --reverse`): four hardcoded placeholder blurbs about yoga and
sleep, every link pointing at `#`, no scraper, no model call, no publish
step, no pruning. The CV at the time described a fully automated pipeline
and called it a production system (quoted in `HANDOFF.md`). The repo is
public, so anyone who clicked through could see the gap.

The pipeline that exists now was written to close that gap. It closes the
"built" half. The "running" half was never true and still isn't.

## 2. Grounding by schema, not by prompt

The writer returns a structured `Draft` (`pipeline/generate.py`) in which
every factual claim carries the index of the source that supports it. An
unattributable claim cannot be represented, so it cannot be published by
accident. Below `min_sources` (2) usable sources the trend is skipped rather
than written from the model's memory (`pipeline/config.py`).

Lesson: a rule the output format enforces beats a rule the prompt asks for.
The prompt can be ignored; the schema can't.

## 3. Three-layer veto

`pipeline/quality.py` runs three checks, cheapest first, and any one rejects
the draft with a stored reason:

1. **Structure** (free): 400-1,600 words, at least 2 sections, a title under
   the length limit, at least one recorded claim, no placeholder text, every
   claim's source index in range, no empty section.
2. **Duplicate** (free): Jaccard similarity of 0.6 or more against any
   published article.
3. **Grounding audit** (a second model call): the auditor reads the draft
   against the sources and scores grounding and clarity 1-5. Both must be 4
   or more, its verdict must be "publish", and any single unsupported claim
   rejects the draft.

Running the free layers first means a broken draft costs no audit call.
The strictness has never been tuned on real output, because there has been
no real output.

## 4. Extraction is the weak link

The writer only sees what `pipeline/sources.py` pulls out of each news page,
so extraction noise turns into article noise. The first live fetch run
(2026-08-21) let unrelated teaser headlines into the grounding text. A
stdlib readability-style extractor replaced the stripper on 2026-10-15, and
on 2026-10-22 it was checked against 8 live pages from 8 hosts: 5 clean,
1 usable with leaks, 1 bad (a video page that picked a teaser card), 1
under-extracted (a live blog kept one post of 99). The bad and the
under-extracted cases were then fixed with synthetic fixtures (2026-10-22,
2026-10-30, 2026-11-03); neither fix has been re-run against the live
pages. The table is in `HANDOFF.md` and on the demo page.

Lesson: test the extractor on real pages early. Fixtures written from
memory of what pages look like missed both failure shapes.

## 5. The CV lesson

Describe what you can show today. "Built and tested offline (69 tests, no
network); never launched" is true and still interesting: the guardrail
design is the story. A claim that the thing runs in production is checkable
in one click (workflow runs, site URL) and fails that check.

The order should have been: one supervised run, then the CV line, not the
other way round.

## 6. Cost model (a ceiling, never measured)

Per trend the pipeline makes two calls to `claude-opus-5`
(`pipeline/config.py`): the writer and the auditor, each with
`max_tokens` = 16,000 and adaptive thinking. Inputs are at most 6 sources
of up to 6,000 characters each (`extract_text` cap), about 36,000
characters, roughly 9,000 tokens at ~4 characters per token (a rule of
thumb, not a count), plus the prompt and, for the auditor, the draft.

At $5 input / $25 output per million tokens for Claude Opus 5
(https://platform.claude.com/docs/en/about-claude/pricing, as listed on
2026-11-03):

| Item | Tokens (ceiling) | Cost |
|---|---|---|
| Writer input | ~12,000 | $0.06 |
| Writer output (cap) | 16,000 | $0.40 |
| Auditor input | ~12,000 | $0.06 |
| Auditor output (cap) | 16,000 | $0.40 |
| **Per trend, ceiling** | | **~$0.92** |
| Per day at `DEPOT_MAX_TRENDS` = 5 | | ~$4.60 |
| Per 30 days | | ~$138 |

Real runs would cost less (outputs rarely hit the cap, and a trend that
fails the free layers never reaches the auditor), but this has not been
measured. The first supervised run should log `response.usage` for both
calls and replace this table.

## What I'd do differently

- Run once, end to end, with a real key, before writing anything public
  about it.
- Spend the first week on extraction against real pages, not on the gate.
- Keep a spend cap on the key from day one, so "one supervised run" is a
  small, safe decision instead of an open-ended one.
