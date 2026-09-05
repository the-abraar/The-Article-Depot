"""Idempotency and resume — what makes a double-fire safe."""

from __future__ import annotations

from pipeline.store import Store


def test_same_trend_same_day_claimed_once(store: Store):
    assert store.claim_article("mars", "2026-08-21") is not None
    assert store.claim_article("mars", "2026-08-21") is None


def test_same_trend_next_day_is_a_new_claim(store: Store):
    a = store.claim_article("mars", "2026-08-21")
    b = store.claim_article("mars", "2026-08-22")
    assert a is not None and b is not None and a != b


def test_unfinished_rows_are_resumable(store: Store):
    a = store.claim_article("mars", "2026-08-21")
    b = store.claim_article("venus", "2026-08-21")
    store.set_state(b, "published")
    resumable = [r["id"] for r in store.resumable("2026-08-21")]
    assert resumable == [a]


def test_terminal_states_are_not_resumable(store: Store):
    for i, state in enumerate(("published", "rejected", "skipped")):
        aid = store.claim_article(f"t{i}", "2026-08-21")
        store.set_state(aid, state)
    assert store.resumable("2026-08-21") == []


def test_article_for_finds_any_state(store: Store):
    aid = store.claim_article("mars", "2026-08-21")
    assert store.article_for("mars", "2026-08-21")["id"] == aid
    assert store.article_for("venus", "2026-08-21") is None


def test_slug_collision_detected(store: Store):
    aid = store.claim_article("mars", "2026-08-21")
    store.save_draft(aid, "Mars", "mars-rover", {"title": "Mars"})
    assert store.slug_exists("mars-rover")
    assert not store.slug_exists("venus-rover")


def test_sources_are_replaced_not_appended(store: Store):
    aid = store.claim_article("mars", "2026-08-21")
    one = [{"url": "u1", "title": "t", "publisher": "p", "text": "x"}]
    store.save_sources(aid, one)
    store.save_sources(aid, one)
    assert len(store.sources_for(aid)) == 1


def test_trend_last_seen_advances(store: Store):
    store.record_trend("mars", "Mars", "50,000+", "2026-08-01")
    store.record_trend("mars", "Mars", None, "2026-08-20")
    assert store.trend_last_seen("mars") == "2026-08-20"


def test_published_excludes_pruned_by_default(store: Store):
    aid = store.claim_article("mars", "2026-08-21")
    store.save_draft(aid, "M", "m", {"title": "M"})
    store.mark_published(aid)
    assert len(store.published()) == 1
    store.mark_pruned(aid, "stale")
    assert store.published() == []
    assert len(store.published(include_pruned=True)) == 1
