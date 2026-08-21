"""The gate between "a draft exists" and "it is on the public site".

Three layers, cheapest first, and every one of them can veto:

1. Structural  -- length, shape, placeholder text, claims that point at
   sources which do not exist. Pure Python, no API call.
2. Duplicate   -- token overlap against everything already published, so a
   trend that resurfaces does not produce the same article twice.
3. Grounding   -- a second Claude call that reads the draft *against the
   sources* and scores whether each claim is actually supported. This is the
   layer that catches a fluent, well-shaped hallucination.

A draft publishes only if all three pass. Anything else is recorded with a
reason and left unpublished.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from .generate import Draft, full_text, word_count

log = logging.getLogger(__name__)

PLACEHOLDER = re.compile(
    r"\b(lorem ipsum|tk tk|\btbd\b|placeholder|as an ai language model|"
    r"i cannot|todo:|xxx)\b", re.IGNORECASE)

_WORD = re.compile(r"[a-z0-9']+")
_STOP = frozenset("""a an and are as at be by for from has have in is it its of on
that the to was were will with this these those he she they them their but or""".split())


@dataclass
class Verdict:
    passed: bool
    failures: list[str] = field(default_factory=list)
    audit: dict = field(default_factory=dict)

    def fail(self, reason: str) -> None:
        self.passed = False
        self.failures.append(reason)


# --- layer 1: structure ----------------------------------------------------

def check_structure(draft: Draft, sources: list, cfg, verdict: Verdict) -> None:
    words = word_count(draft)
    if words < cfg.min_words:
        verdict.fail(f"too short: {words} words < {cfg.min_words}")
    if words > cfg.max_words:
        verdict.fail(f"too long: {words} words > {cfg.max_words}")
    if len(draft.sections) < cfg.min_sections:
        verdict.fail(f"too few sections: {len(draft.sections)} < {cfg.min_sections}")
    if not draft.title.strip():
        verdict.fail("empty title")
    if len(draft.title) > 120:
        verdict.fail(f"title too long: {len(draft.title)} chars")
    if not draft.claims:
        verdict.fail("no claims recorded — nothing is attributable")

    text = full_text(draft)
    hit = PLACEHOLDER.search(text)
    if hit:
        verdict.fail(f"placeholder text present: {hit.group(0)!r}")

    # A claim citing a source that was never fetched is a broken chain of
    # custody even if the sentence happens to be true.
    for claim in draft.claims:
        if not 0 <= claim.source_index < len(sources):
            verdict.fail(
                f"claim cites source {claim.source_index}, only "
                f"{len(sources)} available: {claim.text[:60]!r}")
            break

    if any(not s.paragraphs for s in draft.sections):
        verdict.fail("a section has no paragraphs")


# --- layer 2: duplicates ---------------------------------------------------

def tokens(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 2}


def similarity(a: str, b: str) -> float:
    """Jaccard overlap on content words. Cheap, and enough to catch a rerun."""
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def check_duplicate(draft: Draft, published_texts: list[str], cfg,
                    verdict: Verdict) -> None:
    text = full_text(draft)
    for other in published_texts:
        score = similarity(text, other)
        if score >= cfg.dupe_threshold:
            verdict.fail(f"duplicate of an existing article (similarity {score:.2f})")
            return


# --- layer 3: grounding audit ----------------------------------------------

class ClaimCheck(BaseModel):
    claim: str
    supported: bool = Field(
        description="True only if the cited source explicitly supports this claim.")
    note: str = Field(description="If unsupported, what the source actually says.")


class Audit(BaseModel):
    grounding_score: int = Field(
        description="1-5: how well the article's claims are supported by the sources.")
    clarity_score: int = Field(description="1-5: how clear and readable the article is.")
    unsupported_claims: list[ClaimCheck] = Field(
        description="Every claim that the cited source does not actually support.")
    verdict: str = Field(description="One of: publish, revise, reject.")


AUDITOR_SYSTEM = """You are a fact-checker reviewing a draft before it publishes \
with no human in the loop.

You receive the source excerpts a writer was given, and the draft they produced.

Check each claim in the draft against the source it cites. A claim is supported \
only if that source explicitly states it -- not if it is plausible, not if it is \
probably true, not if a different source might support it.

Be strict. Assume the writer may have introduced fluent, confident detail that \
is not in the sources; that is exactly what you are here to catch. Score \
grounding 1-5, where 5 means every claim is directly supported and 1 means the \
draft is substantially invented. Return every unsupported claim you find."""


def _audit_prompt(draft: Draft, sources: list) -> str:
    src = "\n\n".join(
        f"--- SOURCE {i} ---\n{s.text}" for i, s in enumerate(sources))
    claims = "\n".join(
        f"- [cites source {c.source_index}] {c.text}" for c in draft.claims)
    body = "\n\n".join(
        f"## {s.heading}\n" + "\n".join(s.paragraphs) for s in draft.sections)
    return (f"{src}\n\n=== DRAFT ===\nTitle: {draft.title}\nDek: {draft.dek}\n\n"
            f"{body}\n\n=== CLAIMS THE WRITER RECORDED ===\n{claims}")


def audit(draft: Draft, sources: list, cfg, client) -> Audit:
    response = client.messages.parse(
        model=cfg.auditor_model,
        max_tokens=cfg.max_tokens,
        system=AUDITOR_SYSTEM,
        thinking={"type": "adaptive"},
        messages=[{"role": "user", "content": _audit_prompt(draft, sources)}],
        output_format=Audit,
    )
    if getattr(response, "stop_reason", None) == "refusal":
        raise RuntimeError("auditor refused to review the draft")
    result = response.parsed_output
    if result is None:
        raise RuntimeError("auditor produced no parsed verdict")
    return result


def check_grounding(draft: Draft, sources: list, cfg, client,
                    verdict: Verdict) -> None:
    result = audit(draft, sources, cfg, client)
    verdict.audit = result.model_dump()

    if result.grounding_score < cfg.min_audit_score:
        verdict.fail(f"grounding score {result.grounding_score} "
                     f"< {cfg.min_audit_score}")
    if result.clarity_score < cfg.min_audit_score:
        verdict.fail(f"clarity score {result.clarity_score} "
                     f"< {cfg.min_audit_score}")
    if result.verdict.lower() != "publish":
        verdict.fail(f"auditor said {result.verdict!r}")
    if result.unsupported_claims:
        verdict.fail(f"{len(result.unsupported_claims)} unsupported claim(s): "
                     + "; ".join(c.claim[:60] for c in result.unsupported_claims[:3]))


# --- the gate ---------------------------------------------------------------

def evaluate(draft: Draft, sources: list, published_texts: list[str], cfg,
             client) -> Verdict:
    """Run every layer. Cheap checks first; the API call only if they pass."""
    verdict = Verdict(passed=True)

    check_structure(draft, sources, cfg, verdict)
    check_duplicate(draft, published_texts, cfg, verdict)
    if not verdict.passed:
        return verdict                    # don't pay for an audit of a dead draft

    check_grounding(draft, sources, cfg, client, verdict)
    return verdict
