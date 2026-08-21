"""The gate must actually stop things. Each test drives one veto."""

from __future__ import annotations

import pytest

from conftest import FakeClient, FakeParsed, make_audit, make_draft
from pipeline.generate import Claim, full_text
from pipeline.quality import ClaimCheck, Verdict, check_structure, evaluate, similarity


def test_good_draft_publishes(cfg, sources):
    client = FakeClient([FakeParsed(make_audit())])
    v = evaluate(make_draft(), sources, [], cfg, client)
    assert v.passed, v.failures


def test_short_draft_rejected(cfg, sources):
    v = evaluate(make_draft(words=50), sources, [], cfg, FakeClient([]))
    assert not v.passed
    assert any("too short" in f for f in v.failures)


def test_overlong_draft_rejected(cfg, sources):
    v = evaluate(make_draft(words=5000), sources, [], cfg, FakeClient([]))
    assert not v.passed
    assert any("too long" in f for f in v.failures)


def test_too_few_sections_rejected(cfg, sources):
    v = evaluate(make_draft(sections=1), sources, [], cfg, FakeClient([]))
    assert not v.passed
    assert any("too few sections" in f for f in v.failures)


def test_claim_citing_missing_source_rejected(cfg, sources):
    """Two sources exist; a claim citing index 7 is a broken chain of custody."""
    draft = make_draft(source_index=7)
    v = evaluate(draft, sources, [], cfg, FakeClient([]))
    assert not v.passed
    assert any("cites source 7" in f for f in v.failures)


def test_draft_with_no_claims_rejected(cfg, sources):
    draft = make_draft()
    draft.claims = []
    v = evaluate(draft, sources, [], cfg, FakeClient([]))
    assert not v.passed
    assert any("no claims" in f for f in v.failures)


def test_placeholder_text_rejected(cfg, sources):
    draft = make_draft()
    draft.sections[0].paragraphs.append(
        "As an AI language model, I cannot verify this. " * 5)
    v = evaluate(draft, sources, [], cfg, FakeClient([]))
    assert not v.passed
    assert any("placeholder" in f for f in v.failures)


def test_duplicate_of_published_rejected(cfg, sources):
    draft = make_draft()
    v = evaluate(draft, sources, [full_text(draft)], cfg, FakeClient([]))
    assert not v.passed
    assert any("duplicate" in f for f in v.failures)


def test_cheap_checks_short_circuit_the_api(cfg, sources):
    """A draft that fails structurally must not cost an audit call."""
    client = FakeClient([])          # any API call raises "ran out of responses"
    v = evaluate(make_draft(words=10), sources, [], cfg, client)
    assert not v.passed
    assert client.messages.calls == []


def test_low_grounding_score_rejected(cfg, sources):
    client = FakeClient([FakeParsed(make_audit(grounding=2))])
    v = evaluate(make_draft(), sources, [], cfg, client)
    assert not v.passed
    assert any("grounding score 2" in f for f in v.failures)


def test_unsupported_claim_rejected_even_with_high_score(cfg, sources):
    """A fluent hallucination scores well on clarity; the claim list vetoes it."""
    audit = make_audit(unsupported=[ClaimCheck(
        claim="The mission cost $4.2 billion",
        supported=False,
        note="No source mentions a cost figure.")])
    client = FakeClient([FakeParsed(audit)])
    v = evaluate(make_draft(), sources, [], cfg, client)
    assert not v.passed
    assert any("unsupported claim" in f for f in v.failures)


def test_auditor_revise_verdict_rejected(cfg, sources):
    client = FakeClient([FakeParsed(make_audit(verdict="revise"))])
    v = evaluate(make_draft(), sources, [], cfg, client)
    assert not v.passed


def test_audit_recorded_even_on_failure(cfg, sources):
    client = FakeClient([FakeParsed(make_audit(grounding=1))])
    v = evaluate(make_draft(), sources, [], cfg, client)
    assert v.audit["grounding_score"] == 1


def test_similarity_is_symmetric_and_bounded():
    a, b = "the rover reached the rim", "a bakery opened in leeds"
    assert similarity(a, b) == similarity(b, a)
    assert 0.0 <= similarity(a, b) <= 1.0
    assert similarity(a, a) == 1.0
