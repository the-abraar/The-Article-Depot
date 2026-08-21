"""Grounded article generation.

The model is given the fetched source text and nothing else, and the output
schema forces every factual claim to name the source index it came from.
Structured output does the enforcing: a draft that cannot attribute a claim
cannot be represented, so grounding is a property of the shape, not of the
model remembering an instruction.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

from .trends import slugify

log = logging.getLogger(__name__)


class Claim(BaseModel):
    """One factual assertion, tied to the source that supports it."""
    text: str = Field(description="The factual claim as stated in the article.")
    source_index: int = Field(
        description="0-based index of the source supporting this claim.")


class Section(BaseModel):
    heading: str
    paragraphs: list[str]


class Draft(BaseModel):
    title: str = Field(description="Headline, under 80 characters, no clickbait.")
    dek: str = Field(description="One-sentence standfirst summarising the piece.")
    sections: list[Section]
    claims: list[Claim] = Field(
        description="Every factual claim in the article, each tied to a source.")


SYSTEM = """You write news explainers for The Article Depot.

You will be given a trending topic and numbered source excerpts. These sources \
are the ONLY facts available to you.

Rules, in order of importance:
1. Every factual claim must be supported by the supplied sources. If the \
sources do not establish something, do not write it -- not from background \
knowledge, not as a plausible inference, not as a hedge.
2. Record each factual claim in `claims` with the 0-based index of the source \
that supports it. A claim you cannot attribute must be cut from the article.
3. Do not invent quotes, numbers, dates, names, or attributions.
4. Where the sources disagree or a detail is unconfirmed, say so plainly.
5. Neutral, plain, specific prose. No filler, no "in today's fast-paced world", \
no padding to reach a length.
6. Write for a reader who has not seen the news yet.

Return a title, a one-sentence dek, and 2-5 sections of 1-3 paragraphs each."""


class RefusalError(RuntimeError):
    """The model declined the request; the caller should skip this trend."""


def _sources_block(sources: list[Any]) -> str:
    parts = []
    for i, s in enumerate(sources):
        publisher = getattr(s, "publisher", "") or "unknown outlet"
        title = getattr(s, "title", "") or ""
        parts.append(
            f"--- SOURCE {i} ---\n"
            f"Publisher: {publisher}\nHeadline: {title}\nURL: {s.url}\n\n{s.text}")
    return "\n\n".join(parts)


def build_prompt(trend, sources: list[Any]) -> str:
    return (
        f"Trending topic: {trend.title}\n"
        f"Search interest: {trend.traffic or 'not reported'}\n\n"
        f"You have {len(sources)} source excerpts.\n\n"
        f"{_sources_block(sources)}\n\n"
        f"Write the explainer for this topic using only these sources.")


def generate(trend, sources: list[Any], cfg, client) -> Draft:
    """Ask Claude for a grounded draft. Raises RefusalError if it declines."""
    response = client.messages.parse(
        model=cfg.model,
        max_tokens=cfg.max_tokens,
        system=SYSTEM,
        thinking={"type": "adaptive"},
        messages=[{"role": "user", "content": build_prompt(trend, sources)}],
        output_format=Draft,
    )

    # Safety classifiers can decline with HTTP 200 -- check before reading content.
    if getattr(response, "stop_reason", None) == "refusal":
        detail = getattr(response, "stop_details", None)
        raise RefusalError(
            f"model refused: {getattr(detail, 'category', 'unspecified')}")

    draft = response.parsed_output
    if draft is None:
        raise RuntimeError("structured output produced no parsed draft")
    return draft


def draft_slug(draft: Draft, store=None) -> str:
    """Stable URL slug, de-collided against anything already published."""
    base = slugify(draft.title)
    if store is None or not store.slug_exists(base):
        return base
    n = 2
    while store.slug_exists(f"{base}-{n}"):
        n += 1
    return f"{base}-{n}"


def word_count(draft: Draft) -> int:
    return sum(len(p.split()) for s in draft.sections for p in s.paragraphs)


def full_text(draft: Draft) -> str:
    parts = [draft.title, draft.dek]
    for s in draft.sections:
        parts.append(s.heading)
        parts.extend(s.paragraphs)
    return "\n".join(parts)
