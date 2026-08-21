"""Trend ingestion: Google Trends daily RSS -> normalised Trend records.

The RSS feed is used rather than a scraped HTML page for two reasons: it is
a published, stable interface, and each item already carries the news stories
behind the trend. Those stories become the grounding sources, so the pipeline
never has to guess where a trend came from.
"""

from __future__ import annotations

import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

NS = {"ht": "https://trends.google.com/trending/rss"}


@dataclass
class NewsItem:
    title: str
    url: str
    snippet: str = ""
    publisher: str = ""


@dataclass
class Trend:
    title: str
    slug: str
    traffic: str | None = None
    news: list[NewsItem] = field(default_factory=list)


def slugify(text: str, max_len: int = 60) -> str:
    """URL-safe slug. Also the trend's stable identity across runs."""
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    text = re.sub(r"-{2,}", "-", text)
    return text[:max_len].strip("-") or "untitled"


def _text(node, path: str) -> str:
    found = node.find(path, NS)
    return (found.text or "").strip() if found is not None and found.text else ""


def parse_feed(xml_text: str) -> list[Trend]:
    """Parse the Trends RSS body. Malformed items are skipped, not fatal."""
    root = ET.fromstring(xml_text)
    trends: list[Trend] = []

    for item in root.iter("item"):
        title = _text(item, "title")
        if not title:
            continue
        traffic = _text(item, "ht:approx_traffic") or None

        news: list[NewsItem] = []
        for n in item.findall("ht:news_item", NS):
            url = _text(n, "ht:news_item_url")
            if not url:
                continue
            news.append(NewsItem(
                title=_text(n, "ht:news_item_title") or title,
                url=url,
                snippet=_text(n, "ht:news_item_snippet"),
                publisher=_text(n, "ht:news_item_source"),
            ))

        trends.append(Trend(title=title, slug=slugify(title),
                            traffic=traffic, news=news))
    return trends


def feed_url(cfg) -> str:
    return f"{cfg.trends_url}?geo={cfg.geo}"


def fetch_trends(cfg, fetcher) -> list[Trend]:
    """Fetch and parse today's trends, most-trafficked first as the feed orders them."""
    result = fetcher.get(feed_url(cfg))
    return parse_feed(result.text)
