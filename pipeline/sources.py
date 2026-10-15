"""Turn a trend's news links into grounding text.

Only what comes back from here may appear in an article. If too few sources
survive fetching, the trend is skipped rather than written from the model's
own recall -- that choice is what keeps "runs unattended" defensible.
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

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
    """Reduce an HTML page to the article's own prose.

    First pass is a readability-style main-content pick (_article_text): find
    the one container the article's paragraphs cluster in, so teaser rails,
    "more from" modules and nav that sit beside it never reach the grounding.
    Pages it cannot read (no real <p> prose) fall back to the plain stripper.
    Deliberately dependency-free: stdlib html.parser only, so the pipeline's
    install surface stays the Anthropic SDK alone.
    """
    try:
        text = _article_text(raw_html)
    except RecursionError:              # pathological nesting: tag soup
        text = ""
    if len(text) < 200:                 # same floor gather() uses for a stub
        text = _strip_text(raw_html)
    return text[:max_chars]


# --- main-content extraction ------------------------------------------------

_SKIP = {"script", "style", "noscript", "template", "svg", "nav", "header",
         "footer", "aside", "form", "button", "select", "iframe", "figure"}
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
         "meta", "param", "source", "track", "wbr"}
_BLOCK = {"p", "div", "section", "article", "main", "h1", "h2", "h3", "h4",
          "h5", "h6", "li", "ul", "ol", "tr", "table", "blockquote", "pre",
          "br", "dd", "dt", "dl", "figcaption", "hr"}
# class/id words that mark page furniture rather than the story.
_UNLIKELY = re.compile(
    r"comment|related|sidebar|share|social|newsletter|promo|recommend|"
    r"sponsor|teaser|trending|more-from|breadcrumb|cookie|banner|menu|"
    r"popup|subscribe|advert|\bads?\b", re.IGNORECASE)
_LIKELY = re.compile(r"article|body|content|main|story", re.IGNORECASE)


@dataclass(eq=False)
class _Node:
    tag: str
    parent: "_Node | None" = None
    children: list = field(default_factory=list)     # _Node or str
    skip: bool = False
    score: float = 0.0


class _TreeBuilder(HTMLParser):
    """Tolerant DOM: unclosed tags close when an ancestor closes."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("#root")
        self.stack = [self.root]
        self.paragraphs: list[_Node] = []

    def handle_starttag(self, tag, attrs):
        if tag == "p" and self.stack[-1].tag == "p":    # <p>a<p>b
            self.stack.pop()
        parent = self.stack[-1]
        hint = " ".join(v or "" for k, v in attrs if k in ("class", "id"))
        unlikely = (tag not in ("html", "body", "article", "main")
                    and _UNLIKELY.search(hint) and not _LIKELY.search(hint))
        node = _Node(tag, parent, skip=tag in _SKIP or bool(unlikely))
        parent.children.append(node)
        if tag in ("p", "pre"):
            self.paragraphs.append(node)
        if tag not in _VOID:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def _blocks(node: _Node) -> list[tuple[str, int]]:
    """(text, chars inside links) per block-level run under `node`."""
    out: list[tuple[str, int]] = []
    buf: list[str] = []
    link = [0]

    def flush():
        text = " ".join("".join(buf).split())
        if text:
            out.append((text, min(link[0], len(text))))
        buf.clear()
        link[0] = 0

    def walk(n: _Node, in_link: bool):
        if n.skip:
            return
        if n.tag in _BLOCK:
            flush()
        for c in n.children:
            if isinstance(c, str):
                buf.append(c)
                if in_link:
                    link[0] += len(c.strip())
            else:
                walk(c, in_link or c.tag == "a")
        if n.tag in _BLOCK:
            flush()

    walk(node, node.tag == "a")
    flush()
    return out


def _skipped(node: _Node) -> bool:
    while node is not None:
        if node.skip:
            return True
        node = node.parent
    return False


def _article_text(raw_html: str) -> str:
    """Pick the container the article's paragraphs cluster in.

    Each prose paragraph votes for its parent (full score) and grandparent
    (half), as in Arc90 Readability. A body split across sibling divs still
    wins through the shared grandparent, while a teaser rail -- many short
    one-line items -- collects little. The winner's score is discounted by
    its link density, so a block of linked headlines cannot win on volume.
    shortcut: a body split across exactly two sibling divs is merged by score;
    odd layouts beyond that fall to the auditor -- add fixtures as they appear.
    """
    tb = _TreeBuilder()
    tb.feed(raw_html)
    tb.close()

    candidates: list[_Node] = []
    for p in tb.paragraphs:
        if _skipped(p):
            continue
        text = " ".join(t for t, _ in _blocks(p))
        if len(text) < 25:
            continue
        vote = 1 + text.count(",") + min(len(text) / 100, 3)
        for node, share in ((p.parent, 1.0), (p.parent and p.parent.parent, 0.5)):
            if node is None or node is tb.root:
                continue
            if node.score == 0:
                candidates.append(node)
            node.score += vote * share
    if not candidates:
        return ""

    def final(n: _Node) -> float:
        blocks = _blocks(n)
        total = sum(len(t) for t, _ in blocks) or 1
        return n.score * (1 - sum(k for _, k in blocks) / total)

    scores = {n: final(n) for n in candidates}
    top = max(candidates, key=scores.__getitem__)
    # A body split in two sibling divs: the twin scores close to the winner,
    # a teaser item or promo beside it does not.
    parts = [n for n in top.parent.children
             if n is top or scores.get(n, 0) >= scores[top] / 2]
    # Inside the winner, drop link lists ("Read more: ...") and short crumbs.
    keep = [t for n in parts for t, k in _blocks(n)
            if len(t) > 40 and k / len(t) <= 0.5]
    return "\n\n".join(keep)


# --- fallback: plain stripper -----------------------------------------------

def _strip_text(raw_html: str) -> str:
    """The original heuristic, for pages without paragraph markup."""
    text = re.sub(r"<!--.*?-->", " ", raw_html, flags=re.DOTALL)
    text = _DROP_BLOCKS.sub(" ", text)
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
    return _main_block(text)


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
