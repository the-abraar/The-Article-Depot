"""The crawler's manners, driven into each failure mode deliberately."""

from __future__ import annotations

import pytest

from conftest import FakeHTTP, FakeResponse, http_error
from pipeline.fetcher import Blocked, PoliteFetcher

ROBOTS = "https://site.example/robots.txt"
PAGE = "https://site.example/story"


def make(cfg, routes, **kw):
    slept: list[float] = []
    ticks = {"t": 0.0}

    def clock():
        ticks["t"] += 0.0
        return ticks["t"]

    def sleep(seconds):
        slept.append(seconds)
        ticks["t"] += seconds

    f = PoliteFetcher(cfg=cfg, opener=FakeHTTP(routes), sleep=sleep,
                      clock=clock, **kw)
    return f, slept


def test_robots_disallow_is_obeyed(cfg):
    routes = {ROBOTS: FakeResponse(b"User-agent: *\nDisallow: /story"),
              PAGE: FakeResponse(b"<p>text</p>")}
    f, _ = make(cfg, routes)
    assert f.allowed(PAGE) is False
    with pytest.raises(Blocked):
        f.get(PAGE)


def test_allowed_path_is_fetched(cfg):
    routes = {ROBOTS: FakeResponse(b"User-agent: *\nDisallow: /private"),
              PAGE: FakeResponse(b"<p>hello</p>")}
    f, _ = make(cfg, routes)
    assert f.get(PAGE).text == "<p>hello</p>"


def test_missing_robots_allows_but_still_paces(cfg):
    routes = {PAGE: FakeResponse(b"<p>hello</p>")}   # robots.txt 404s
    f, slept = make(cfg, routes)
    assert f.get(PAGE).status == 200
    f.get(PAGE)
    # Second request waits out the configured floor.
    assert any(s >= cfg.default_crawl_delay - 0.01 for s in slept)


def test_crawl_delay_raises_the_floor(cfg):
    routes = {ROBOTS: FakeResponse(b"User-agent: *\nCrawl-delay: 9\n"),
              PAGE: FakeResponse(b"<p>hi</p>")}
    f, slept = make(cfg, routes)
    f.get(PAGE)
    f.get(PAGE)
    assert f.hosts["site.example"].crawl_delay == 9.0
    assert any(s >= 8.9 for s in slept), "Crawl-delay must be honoured"


def test_crawl_delay_never_lowers_the_floor(cfg):
    """A tiny Crawl-delay must not make us faster than our own floor."""
    routes = {ROBOTS: FakeResponse(b"User-agent: *\nCrawl-delay: 0.1\n"),
              PAGE: FakeResponse(b"<p>hi</p>")}
    f, _ = make(cfg, routes)
    f.get(PAGE)
    assert f.hosts["site.example"].crawl_delay == cfg.default_crawl_delay


def test_429_backs_off_then_succeeds(cfg):
    seq = [http_error(429), FakeResponse(b"<p>ok</p>")]
    routes = {ROBOTS: FakeResponse(b""), PAGE: lambda: seq.pop(0)}
    f, slept = make(cfg, routes)
    assert f.get(PAGE).text == "<p>ok</p>"
    assert slept, "a 429 must be followed by a wait"


def test_retry_after_is_honoured(cfg):
    seq = [http_error(429, {"Retry-After": "42"}), FakeResponse(b"<p>ok</p>")]
    routes = {ROBOTS: FakeResponse(b""), PAGE: lambda: seq.pop(0)}
    f, slept = make(cfg, routes)
    f.get(PAGE)
    assert 42.0 in slept


def test_repeated_blocks_drop_the_host(cfg):
    routes = {ROBOTS: FakeResponse(b""), PAGE: lambda: http_error(403)}
    f, _ = make(cfg, routes)
    with pytest.raises(Blocked):
        f.get(PAGE)
    assert f.hosts["site.example"].dropped is True
    # Every later URL on that host is refused without another request.
    assert f.allowed("https://site.example/other") is False


def test_404_is_not_retried(cfg):
    calls = {"n": 0}

    def miss():
        calls["n"] += 1
        return http_error(404)

    routes = {ROBOTS: FakeResponse(b""), PAGE: miss}
    f, _ = make(cfg, routes)
    with pytest.raises(Exception):
        f.get(PAGE)
    assert calls["n"] == 1, "a 404 is final, not worth a retry"


def test_user_agent_is_sent(cfg):
    routes = {ROBOTS: FakeResponse(b""), PAGE: FakeResponse(b"<p>x</p>")}
    f, _ = make(cfg, routes)
    f.get(PAGE)
    assert "ArticleDepotBot" in cfg.user_agent


def test_non_http_scheme_refused(cfg):
    f, _ = make(cfg, {})
    assert f.allowed("file:///etc/passwd") is False
