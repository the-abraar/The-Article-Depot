"""Stage orchestration for one unattended run.

Order: ingest trends -> per trend (claim, gather sources, generate, gate,
publish) -> prune -> rebuild the site.

Every trend is independent and every failure is contained: one trend that
cannot be sourced, generated, or verified is recorded with a reason and the
run continues. A run that dies halfway leaves claimed-but-unfinished rows,
which the next run picks up instead of restarting -- and because claiming is
a UNIQUE insert, a double-fire on the same day does no duplicate work.
"""

from __future__ import annotations

import json
import logging

from . import prune as prune_mod
from . import publish as publish_mod
from . import quality, sources as sources_mod, trends as trends_mod
from .fetcher import Blocked, PoliteFetcher
from .generate import RefusalError, draft_slug, full_text, generate
from .store import TERMINAL_STATES, Store, today

log = logging.getLogger(__name__)


def _client(cfg):
    """Constructed lazily so ingestion and pruning work without credentials."""
    import anthropic
    return anthropic.Anthropic()


def process_trend(trend, store, cfg, fetcher, client, published_texts) -> str:
    """Run one trend end to end. Returns the terminal state it reached."""
    run_date = today()
    article_id = store.claim_article(trend.slug, run_date)
    if article_id is None:
        # Not a fresh claim -- either already finished today, or a previous
        # run claimed it and died before reaching a terminal state. The
        # latter must be resumed, not skipped, or a mid-run crash silently
        # drops that trend for the rest of the day.
        existing = store.article_for(trend.slug, run_date)
        if existing is None or existing["state"] in TERMINAL_STATES:
            log.info("%s already handled for %s", trend.slug, run_date)
            return "duplicate"
        article_id = existing["id"]
        log.info("resuming %s for %s (was %r)",
                 trend.slug, run_date, existing["state"])

    # --- grounding sources -------------------------------------------------
    found = sources_mod.gather(trend, fetcher, cfg)
    if len(found) < cfg.min_sources:
        reason = f"only {len(found)} usable source(s), need {cfg.min_sources}"
        store.set_state(article_id, "skipped", reason=reason)
        log.info("skip %s: %s", trend.slug, reason)
        return "skipped"
    store.save_sources(article_id, [s.as_dict() for s in found])

    # --- generation --------------------------------------------------------
    try:
        draft = generate(trend, found, cfg, client)
    except RefusalError as exc:
        store.set_state(article_id, "skipped", reason=str(exc))
        return "skipped"
    except Exception as exc:
        store.set_state(article_id, "skipped", reason=f"generation failed: {exc}")
        log.warning("generation failed for %s: %s", trend.slug, exc)
        return "skipped"

    slug = draft_slug(draft, store)
    store.save_draft(article_id, draft.title, slug, draft.model_dump())

    # --- the gate ----------------------------------------------------------
    try:
        verdict = quality.evaluate(draft, found, published_texts, cfg, client)
    except Exception as exc:
        store.set_state(article_id, "rejected", reason=f"audit failed: {exc}")
        log.warning("audit failed for %s: %s", trend.slug, exc)
        return "rejected"

    if verdict.audit:
        store.save_audit(article_id, verdict.audit)

    if not verdict.passed:
        reason = "; ".join(verdict.failures)
        store.set_state(article_id, "rejected", reason=reason)
        log.info("rejected %s: %s", trend.slug, reason)
        return "rejected"

    store.mark_published(article_id)
    published_texts.append(full_text(draft))
    log.info("published %s", slug)
    return "published"


def run(cfg, limit: int | None = None, skip_prune: bool = False) -> dict:
    """One full unattended cycle."""
    store = Store(cfg.db_path)
    run_id = store.start_run(today())
    fetcher = PoliteFetcher(cfg=cfg)
    stats = {"trends": 0, "published": 0, "rejected": 0,
             "skipped": 0, "duplicate": 0}

    try:
        try:
            trend_list = trends_mod.fetch_trends(cfg, fetcher)
        except Blocked as exc:
            log.error("trend feed unavailable: %s", exc)
            store.finish_run(run_id, {"error": str(exc)})
            return {"error": str(exc), **stats}

        cap = limit if limit is not None else cfg.max_trends_per_run
        trend_list = trend_list[:cap]
        stats["trends"] = len(trend_list)

        for trend in trend_list:
            store.record_trend(trend.slug, trend.title, trend.traffic, today())

        client = _client(cfg) if trend_list else None
        published_texts = [
            full_text_from_row(row) for row in store.published()]

        for trend in trend_list:
            state = process_trend(trend, store, cfg, fetcher, client,
                                  published_texts)
            stats[state] = stats.get(state, 0) + 1

        if not skip_prune:
            stats["prune"] = prune_mod.prune(store, cfg)

        stats["site"] = publish_mod.publish_site(store, cfg)
        store.finish_run(run_id, stats)
        return stats
    finally:
        store.close()


def full_text_from_row(row) -> str:
    """Reconstruct an article's text from its stored body, for dedupe."""
    body = json.loads(row["body_json"] or "{}")
    parts = [body.get("title", ""), body.get("dek", "")]
    for section in body.get("sections", []):
        parts.append(section.get("heading", ""))
        parts.extend(section.get("paragraphs", []))
    return "\n".join(parts)
