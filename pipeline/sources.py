"""Turn a trend's news links into grounding text.

Only what comes back from here may appear in an article. If too few sources
survive fetching, the trend is skipped rather than written from the model's
own recall -- that choice is what keeps "runs unattended" defensible.
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass

from .fetcher import Blocked

log = logging.getLogger(__name__)

_DROP_BLOCKS = re.compile(
    r"<(script|style|noscript|template|svg|nav|header|footer|aside|form)\b.*?</\1>",
    re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\r\f\v]+")
_BLANKS = re.compile(r"\n{3,}")
# Paragraph-ish boundaries survive as newlines so the model still sees structure.
_BREAKS = re.compile(r"</(p|div|section|article|h[1-6]|li|tr|blockquote)>",
                     re.IGNORECASE)


@dataclass
class Source:
    url: str
    title: str
    publisher: str
    text: str

    def as_dict(self) -> dict:
        return {"url": self.url, "title": self.title,
                "publisher": self.publisher, "text": self.text}


def extract_text(raw_html: str, max_chars: int = 6000) -> str:
    """Strip an HTML page down to readable prose.

    Deliberately dependency-free: a heuristic stripper is enough to give the
    model grounding material, and it keeps the pipeline's install surface to
    the Anthropic SDK alone.
    """
    text = _DROP_BLOCKS.sub(" ", raw_html)
    text = _BREAKS.sub("\n", text)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = _TAG.sub(" ", text)
    text = html.unescape(text)
    text = _WS.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = _BLANKS.sub("\n\n", text).strip()

    # Drop nav crumbs and cookie-banner noise: very short lines carry no claims.
    lines = [ln for ln in text.split("\n") if len(ln) > 40 or not ln]
    text = "\n".join(lines).strip()
    text = _main_block(text)
    return text[:max_chars]


def _main_block(text: str, min_ratio: float = 0.3) -> str:
    """Keep the article body; drop a trailing tail of unrelated teasers.

    A "More from ..." module bolted onto the end of a page survives the
    chrome strip because it isn't nav/aside/footer, just plain divs. It shows
    up as a run of blank-line-separated blocks that are each much smaller
    than the real article body, so once the largest block is found, a
    sharply smaller block after it is the teaser tail, not more of the story.
    """
    blocks = [b for b in re.split(r"\n\s*\n", text) if b.strip()]
    if len(blocks) <= 1:
        return text
    sizes = [len(b) for b in blocks]
    peak = max(range(len(blocks)), key=sizes.__getitem__)
    threshold = sizes[peak] * min_ratio
    end = peak
    for i in range(peak + 1, len(blocks)):
        if sizes[i] < threshold:
            break
        end = i
    return "\n\n".join(blocks[:end + 1])


def gather(trend, fetcher, cfg) -> list[Source]:
    """Fetch each news link for a trend, keeping the ones that yield real text."""
    out: list[Source] = []
    for item in trend.news:
        if len(out) >= 6:
            break
        try:
            result = fetcher.get(item.url)
        except Blocked as exc:
            log.info("skipping %s: %s", item.url, exc)
            continue
        except Exception as exc:
            log.info("failed %s: %s", item.url, exc)
            continue

        if "html" not in (result.content_type or "html").lower():
            continue
        body = extract_text(result.text)
        if len(body) < 200:                 # a stub or a paywall, not a source
            continue
        out.append(Source(url=item.url, title=item.title,
                          publisher=item.publisher, text=body))

    # The snippets in the feed itself are already-published summaries; they
    # backfill grounding when the linked pages are unreachable or paywalled.
    if len(out) < cfg.min_sources:
        for item in trend.news:
            if len(out) >= cfg.min_sources:
                break
            if item.snippet and len(item.snippet) > 80 and \
                    not any(s.url == item.url for s in out):
                out.append(Source(url=item.url, title=item.title,
                                  publisher=item.publisher, text=item.snippet))
    return out
