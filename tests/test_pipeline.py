"""End to end, offline: trends in, static site out."""

from __future__ import annotations

import json

import pytest

from conftest import (FakeClient, FakeHTTP, FakeParsed, FakeResponse,
                      make_audit, make_draft)
from pipeline import run as run_mod
from pipeline.fetcher import PoliteFetcher
from pipeline.publish import publish_site
from pipeline.store import Store, today
from pipeline.trends import NewsItem, Trend

PROSE = ("The rover arrived at the crater rim on Tuesday, the agency said in a "
         "briefing that covered the eleven-day traverse in some detail. ")

FEED = """<?xml version="1.0"?>
<rss xmlns:ht="https://trends.google.com/trending/rss"><channel>
<item><title>Mars Rover</title><ht:approx_traffic>50,000+</ht:approx_traffic>
<ht:news_item><ht:news_item_title>Rover reaches rim</ht:news_item_title>
<ht:news_item_url>https://a.example/1</ht:news_item_url>
<ht:news_item_source>A News</ht:news_item_source></ht:news_item>
<ht:news_item><ht:news_item_title>Traverse complete</ht:news_item_title>
<ht:news_item_url>https://b.example/1</ht:news_item_url>
<ht:news_item_source>B News</ht:news_item_source></ht:news_item>
</item></channel></rss>"""


def routes(cfg):
    body = ("<p>" + PROSE * 4 + "</p>").encode()
    return {
        f"{cfg.trends_url}?geo={cfg.geo}": FakeResponse(FEED.encode(),
                                                        content_type="application/xml"),
        "https://a.example/robots.txt": FakeResponse(b""),
        "https://b.example/robots.txt": FakeResponse(b""),
        "https://a.example/1": FakeResponse(body),
        "https://b.example/1": FakeResponse(body),
    }


@pytest.fixture
def patched(cfg, monkeypatch):
    """Wire the fakes into run.run without touching the network or the API."""
    holder = {}

    def fake_fetcher(**kw):
        return PoliteFetcher(cfg=kw["cfg"], opener=FakeHTTP(routes(cfg)),
                             sleep=lambda s: None, clock=lambda: 0.0)

    monkeypatch.setattr(run_mod, "PoliteFetcher", fake_fetcher)
    monkeypatch.setattr(run_mod, "_client", lambda c: holder["client"])
    return holder


def test_happy_path_publishes_a_page(cfg, patched):
    patched["client"] = FakeClient([
        FakeParsed(make_draft(title="Rover Reaches Crater Rim")),
        FakeParsed(make_audit()),
    ])
    stats = run_mod.run(cfg, limit=1)

    assert stats["published"] == 1, stats
    page = cfg.articles_dir / "rover-reaches-crater-rim.html"
    assert page.exists()
    html = page.read_text()
    assert "Rover Reaches Crater Rim" in html
    assert "a.example" in html and "Sources" in html   # provenance is on the page
    assert '<meta name="description"' in html          # SEO basics present
    listing = (cfg.site_dir / "articles.html").read_text()
    assert "rover-reaches-crater-rim.html" in listing


def test_rejected_draft_never_reaches_the_site(cfg, patched):
    patched["client"] = FakeClient([
        FakeParsed(make_draft()),
        FakeParsed(make_audit(grounding=1, verdict="reject")),
    ])
    stats = run_mod.run(cfg, limit=1)

    assert stats["published"] == 0 and stats["rejected"] == 1
    assert list(cfg.articles_dir.glob("*.html")) == []

    store = Store(cfg.db_path)
    try:
        row = store.conn.execute(
            "SELECT * FROM articles WHERE state='rejected'").fetchone()
        assert "grounding score 1" in row["reason"]
        assert json.loads(row["audit_json"])["verdict"] == "reject"
    finally:
        store.close()


def test_ungrounded_trend_is_skipped_before_any_api_call(cfg, patched):
    """No usable sources means no article — and no generation spend."""
    empty = {f"{cfg.trends_url}?geo={cfg.geo}":
             FakeResponse(FEED.encode(), content_type="application/xml")}

    def fake_fetcher(**kw):
        return PoliteFetcher(cfg=kw["cfg"], opener=FakeHTTP(empty),
                             sleep=lambda s: None, clock=lambda: 0.0)

    import pipeline.run as rm
    rm.PoliteFetcher = fake_fetcher
    client = FakeClient([])            # any call would raise
    patched["client"] = client

    stats = rm.run(cfg, limit=1)
    assert stats["skipped"] == 1
    assert client.messages.calls == []


def test_second_run_same_day_is_a_no_op(cfg, patched):
    patched["client"] = FakeClient([
        FakeParsed(make_draft()), FakeParsed(make_audit())])
    first = run_mod.run(cfg, limit=1)
    assert first["published"] == 1

    # Empty script: a second run that tried to generate again would raise.
    patched["client"] = FakeClient([])
    second = run_mod.run(cfg, limit=1)
    assert second["duplicate"] == 1
    assert second["published"] == 0
    assert len(list(cfg.articles_dir.glob("*.html"))) == 1


def test_publish_is_a_pure_function_of_the_database(cfg, patched):
    patched["client"] = FakeClient([
        FakeParsed(make_draft()), FakeParsed(make_audit())])
    run_mod.run(cfg, limit=1)

    store = Store(cfg.db_path)
    try:
        before = (cfg.articles_dir / "rover-reaches-crater-rim.html").read_text()
        stray = cfg.articles_dir / "orphan-from-an-old-run.html"
        stray.write_text("<p>should not survive</p>")

        publish_site(store, cfg)
        assert not stray.exists(), "pages with no live row must be removed"
        assert (cfg.articles_dir / "rover-reaches-crater-rim.html").read_text() == before
    finally:
        store.close()


def test_prune_removes_the_page_on_next_publish(cfg, patched):
    patched["client"] = FakeClient([
        FakeParsed(make_draft()), FakeParsed(make_audit())])
    run_mod.run(cfg, limit=1)
    page = cfg.articles_dir / "rover-reaches-crater-rim.html"
    assert page.exists()

    store = Store(cfg.db_path)
    try:
        aid = store.published()[0]["id"]
        store.conn.execute(
            "UPDATE articles SET published_at='2020-01-01T00:00:00+00:00' WHERE id=?",
            (aid,))
        store.conn.execute(
            "UPDATE trends SET last_seen='2020-01-02' WHERE slug='mars-rover'")
        store.conn.commit()

        from pipeline.prune import prune
        assert prune(store, cfg)["pruned"] == 1
        publish_site(store, cfg)
        assert not page.exists(), "a pruned article must leave the site"
    finally:
        store.close()


def test_site_has_no_broken_internal_links(cfg, patched):
    """The original site shipped a nav pointing at a file that did not exist."""
    import re
    patched["client"] = FakeClient([
        FakeParsed(make_draft()), FakeParsed(make_audit())])
    run_mod.run(cfg, limit=1)

    pages = list(cfg.site_dir.glob("*.html")) + list(cfg.articles_dir.glob("*.html"))
    assert pages
    for page in pages:
        for href in re.findall(r'href="([^"#:]+\.(?:html|css))"', page.read_text()):
            target = (page.parent / href).resolve()
            assert target.exists(), f"{page.name} links to missing {href}"


def test_run_survives_a_generation_failure(cfg, patched):
    patched["client"] = FakeClient([RuntimeError("api exploded")])
    stats = run_mod.run(cfg, limit=1)
    assert stats["skipped"] == 1
    assert "error" not in stats           # the run itself completed
    assert (cfg.site_dir / "index.html").exists()
