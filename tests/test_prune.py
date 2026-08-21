"""The staleness trigger: both conditions required, each for a reason."""

from __future__ import annotations

from datetime import date, timedelta

from pipeline.prune import is_stale, prune
from pipeline.store import Store

NOW = date(2026, 8, 21)


def row(published: date, slug="a", trend="t"):
    return {"published_at": published.isoformat(), "slug": slug,
            "trend_slug": trend, "id": 1}


def test_old_article_with_quiet_trend_is_stale(cfg):
    published = NOW - timedelta(days=60)
    last_seen = (NOW - timedelta(days=30)).isoformat()
    stale, reason = is_stale(row(published), last_seen, cfg, now=NOW)
    assert stale
    assert "quiet" in reason


def test_recent_article_survives_a_quiet_trend(cfg):
    """The age floor stops a fresh article dying over one quiet fortnight."""
    published = NOW - timedelta(days=3)
    last_seen = (NOW - timedelta(days=90)).isoformat()
    stale, reason = is_stale(row(published), last_seen, cfg, now=NOW)
    assert not stale
    assert "floor" in reason


def test_old_article_survives_while_trend_still_hot(cfg):
    """Age alone is not staleness -- people are still searching for this."""
    published = NOW - timedelta(days=365)
    last_seen = NOW.isoformat()
    stale, reason = is_stale(row(published), last_seen, cfg, now=NOW)
    assert not stale
    assert "threshold" in reason


def test_boundary_is_inclusive(cfg):
    published = NOW - timedelta(days=cfg.prune_min_age_days)
    last_seen = (NOW - timedelta(days=cfg.stale_trend_days)).isoformat()
    stale, _ = is_stale(row(published), last_seen, cfg, now=NOW)
    assert stale, "exactly at both thresholds counts as stale"


def test_one_day_inside_either_threshold_survives(cfg):
    published = NOW - timedelta(days=cfg.prune_min_age_days)
    just_seen = (NOW - timedelta(days=cfg.stale_trend_days - 1)).isoformat()
    assert not is_stale(row(published), just_seen, cfg, now=NOW)[0]

    young = NOW - timedelta(days=cfg.prune_min_age_days - 1)
    quiet = (NOW - timedelta(days=cfg.stale_trend_days)).isoformat()
    assert not is_stale(row(young), quiet, cfg, now=NOW)[0]


def test_unpublished_article_is_never_stale(cfg):
    assert not is_stale({"published_at": None, "slug": "x", "trend_slug": "t"},
                        None, cfg, now=NOW)[0]


def test_trend_never_seen_falls_back_to_age(cfg):
    published = NOW - timedelta(days=90)
    stale, reason = is_stale(row(published), None, cfg, now=NOW)
    assert stale
    assert "never seen" in reason


def test_prune_is_reversible_not_destructive(cfg, store: Store):
    """Pruning unpublishes; the row and its provenance survive."""
    aid = store.claim_article("t", "2026-05-01")
    store.save_draft(aid, "T", "t", {"title": "T", "sections": []})
    store.mark_published(aid)
    store.conn.execute("UPDATE articles SET published_at = ? WHERE id = ?",
                       ("2026-05-01T00:00:00+00:00", aid))
    store.conn.commit()
    store.record_trend("t", "T", None, "2026-05-02")

    result = prune(store, cfg, now=NOW)
    assert result["pruned"] == 1
    assert store.get_article(aid) is not None          # row kept
    assert store.get_article(aid)["pruned_at"] is not None
    assert store.published() == []                     # gone from the live set


def test_dry_run_changes_nothing(cfg, store: Store):
    aid = store.claim_article("t", "2026-05-01")
    store.save_draft(aid, "T", "t", {"title": "T", "sections": []})
    store.mark_published(aid)
    store.conn.execute("UPDATE articles SET published_at = ? WHERE id = ?",
                       ("2026-05-01T00:00:00+00:00", aid))
    store.conn.commit()

    result = prune(store, cfg, now=NOW, dry_run=True)
    assert result["pruned"] == 1
    assert store.get_article(aid)["pruned_at"] is None
    assert len(store.published()) == 1
