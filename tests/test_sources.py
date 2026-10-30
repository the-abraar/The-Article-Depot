"""Grounding material: extraction quality and the min-sources floor."""

from __future__ import annotations

from pathlib import Path

from conftest import FakeHTTP, FakeResponse
from pipeline.fetcher import PoliteFetcher
from pipeline.sources import extract_text, gather
from pipeline.trends import NewsItem, Trend

PROSE = ("The rover arrived at the crater rim on Tuesday, the agency said in a "
         "briefing that lasted about an hour and covered the traverse in detail. ")


def test_scripts_and_chrome_are_stripped():
    page = ("<html><head><script>evil()</script><style>.a{}</style></head>"
            "<body><nav>Home About</nav><p>" + PROSE + "</p>"
            "<footer>Cookie banner</footer></body></html>")
    out = extract_text(page)
    assert "evil()" not in out and ".a{}" not in out
    assert "Cookie banner" not in out           # short line, dropped as chrome
    assert "crater rim" in out


def test_entities_are_unescaped():
    assert "AT&T" in extract_text("<p>" + "AT&amp;T said it would expand. " * 8 + "</p>")


def test_extraction_is_length_capped():
    assert len(extract_text("<p>" + "word " * 20000 + "</p>", max_chars=500)) <= 500


def _fetcher(cfg, routes):
    return PoliteFetcher(cfg=cfg, opener=FakeHTTP(routes), sleep=lambda s: None,
                         clock=lambda: 0.0)


def test_gather_collects_usable_sources(cfg):
    trend = Trend(title="Mars", slug="mars", news=[
        NewsItem(title="A", url="https://a.example/1", publisher="A News"),
        NewsItem(title="B", url="https://b.example/1", publisher="B News"),
    ])
    routes = {
        "https://a.example/robots.txt": FakeResponse(b""),
        "https://b.example/robots.txt": FakeResponse(b""),
        "https://a.example/1": FakeResponse(("<p>" + PROSE * 3 + "</p>").encode()),
        "https://b.example/1": FakeResponse(("<p>" + PROSE * 3 + "</p>").encode()),
    }
    found = gather(trend, _fetcher(cfg, routes), cfg)
    assert len(found) == 2
    assert all(len(s.text) > 200 for s in found)


def test_robots_blocked_source_is_skipped_not_fatal(cfg):
    trend = Trend(title="Mars", slug="mars", news=[
        NewsItem(title="A", url="https://a.example/1", publisher="A"),
        NewsItem(title="B", url="https://b.example/1", publisher="B"),
    ])
    routes = {
        "https://a.example/robots.txt": FakeResponse(b"User-agent: *\nDisallow: /"),
        "https://b.example/robots.txt": FakeResponse(b""),
        "https://b.example/1": FakeResponse(("<p>" + PROSE * 3 + "</p>").encode()),
    }
    found = gather(trend, _fetcher(cfg, routes), cfg)
    assert [s.url for s in found] == ["https://b.example/1"]


def test_feed_snippets_backfill_when_pages_unreachable(cfg):
    """Paywalled links must not leave a trend ungrounded if the feed has text."""
    long_snippet = "The agency confirmed the rover reached the rim on Tuesday. " * 3
    trend = Trend(title="Mars", slug="mars", news=[
        NewsItem(title="A", url="https://a.example/1", snippet=long_snippet),
        NewsItem(title="B", url="https://b.example/1", snippet=long_snippet),
    ])
    routes = {}                       # every fetch 404s
    found = gather(trend, _fetcher(cfg, routes), cfg)
    assert len(found) >= cfg.min_sources
    assert all(s.text for s in found)


def test_trailing_teaser_tail_is_dropped():
    """A 'More from ...' module bolted onto the end must not pollute grounding."""
    article = "<article><p>" + PROSE * 4 + "</p></article>"
    teaser1 = ("<section><div><p>Garth Brooks announces a surprise new tour "
               "for twenty twenty six according to organizers.</p></div></section>")
    teaser2 = ("<section><div><p>Prince Harry was spotted at a charity gala "
               "over the weekend according to reports.</p></div></section>")
    page = "<html><body>" + article + teaser1 + teaser2 + "</body></html>"
    out = extract_text(page)
    assert "crater rim" in out
    assert "Garth Brooks" not in out
    assert "Prince Harry" not in out


def test_stub_pages_are_not_counted_as_sources(cfg):
    trend = Trend(title="Mars", slug="mars", news=[
        NewsItem(title="A", url="https://a.example/1")])
    routes = {"https://a.example/robots.txt": FakeResponse(b""),
              "https://a.example/1": FakeResponse(b"<p>too short</p>")}
    assert gather(trend, _fetcher(cfg, routes), cfg) == []


# --- main-content extraction on synthetic fixtures ---------------------------

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return extract_text((FIXTURES / name).read_text())


def test_teaser_rail_and_nav_stay_out_of_grounding():
    out = _fixture("teaser_rail.html")
    assert out.startswith("The rover arrived at the crater rim")
    assert "sampling arm" in out                     # last paragraph kept
    assert "reroute twice, adding" in out            # inline link text kept
    for noise in ("Garth Brooks", "country singer", "Prince Harry",
                  "Stock futures", "Read more", "Trending", "Copyright",
                  "Sports", "__STATE__", "margin"):
        assert noise not in out, noise


def test_body_split_across_sibling_divs_is_kept_whole():
    out = _fixture("split_body.html")
    assert "seven to two" in out and "eighteen months" in out
    assert out.index("seven to two") < out.index("eighteen months")
    assert "newsletter" not in out                   # promo between the halves
    assert "Police seek" not in out and "Library" not in out


def test_page_without_paragraph_markup_falls_back_to_stripper():
    out = _fixture("no_paragraphs.html")
    assert "ferry terminal" in out and "island route" in out
    assert "Synthetic" not in out                    # HTML comment not leaked
    assert "Contact us" not in out


def test_linked_teaser_cards_do_not_stand_in_for_a_short_page():
    # Live check 2026-10-22: a Yahoo video page (a one-line caption) yielded a
    # teaser card's text, then its whole teaser rail via the fallback.
    out = _fixture("video_teaser_cards.html")
    assert "amateur astronomers" in out
    for noise in ("apple harvest", "bridge", "Night buses"):
        assert noise not in out, noise
    assert len(out) < 200                            # gather() drops it as a stub


def test_live_blog_keeps_every_post():
    # Live check 2026-10-22: a live blog (Le Figaro) kept one post of ~99.
    out = _fixture("live_blog.html")
    for post in ("four seconds", "eight knots", "Line-ups", "wrist injury",
                 "ferry timetable"):
        assert post in out, post
    assert out.index("four seconds") < out.index("ferry timetable")
    assert "marathon" not in out and "Swimming pool" not in out


def test_live_blog_merge_skips_lookalikes_outside_the_feed():
    page = (FIXTURES / "live_blog.html").read_text().replace(
        "</main>", '<div class="extra"><div><div class="post-content"><p>Unrelated '
        "lookalike paragraph about the city budget, which sits outside the "
        "live feed entirely.</p></div></div></div></main>")
    out = extract_text(page)
    assert "four seconds" in out and "ferry timetable" in out
    assert "city budget" not in out
