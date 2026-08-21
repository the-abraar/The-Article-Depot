"""Fakes that let the whole pipeline run offline.

Neither the network nor the Anthropic API is touched by the suite; the
fetcher gets a canned URL->response map and the client gets scripted
structured outputs, so every guardrail can be driven into its failure mode
deliberately.
"""

from __future__ import annotations

import dataclasses
import urllib.error
from pathlib import Path

import pytest

from pipeline.config import Config
from pipeline.generate import Claim, Draft, Section
from pipeline.quality import Audit
from pipeline.store import Store


# --- fake HTTP --------------------------------------------------------------

class FakeResponse:
    def __init__(self, body: bytes, status: int = 200,
                 content_type: str = "text/html"):
        self.body = body
        self.status = status
        self.headers = {"Content-Type": content_type}

    def read(self, n: int = -1) -> bytes:
        return self.body


class FakeHTTP:
    """Maps URL -> FakeResponse, an HTTPError to raise, or a callable."""

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[str] = []

    def __call__(self, request, timeout):
        url = request.full_url
        self.calls.append(url)
        handler = self.routes.get(url)
        if handler is None:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        if callable(handler):
            handler = handler()
        if isinstance(handler, Exception):
            raise handler
        return handler


def http_error(code: int, headers: dict | None = None) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "http://x", code, "blocked", headers or {}, None)


# --- fake Claude ------------------------------------------------------------

class FakeParsed:
    def __init__(self, parsed, stop_reason: str = "end_turn"):
        self.parsed_output = parsed
        self.stop_reason = stop_reason
        self.stop_details = None


class FakeMessages:
    def __init__(self, script: list):
        self.script = list(script)
        self.calls: list[dict] = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if not self.script:
            raise AssertionError("fake client ran out of scripted responses")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeClient:
    def __init__(self, script: list):
        self.messages = FakeMessages(script)


# --- builders ---------------------------------------------------------------

def make_draft(title="Rover Reaches Crater Rim", words=500, sections=3,
               claims=2, source_index=0) -> Draft:
    per = max(1, words // max(1, sections))
    return Draft(
        title=title,
        dek="The rover arrived at the rim after an eleven-day traverse.",
        sections=[
            Section(heading=f"Section {i+1}",
                    paragraphs=[" ".join(["word"] * per)])
            for i in range(sections)
        ],
        claims=[Claim(text=f"claim {i}", source_index=source_index)
                for i in range(claims)],
    )


def make_audit(grounding=5, clarity=5, verdict="publish",
               unsupported=None) -> Audit:
    return Audit(grounding_score=grounding, clarity_score=clarity,
                 verdict=verdict, unsupported_claims=unsupported or [])


@dataclasses.dataclass
class FakeSource:
    url: str = "https://news.example.com/a"
    title: str = "NASA confirms schedule"
    publisher: str = "Example News"
    text: str = "The rover arrived at the crater rim on Tuesday. " * 10

    def as_dict(self):
        return dataclasses.asdict(self)


# --- fixtures ---------------------------------------------------------------

@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    return Config(
        db_path=tmp_path / "depot.db",
        site_dir=tmp_path / "site",
        template_dir=Path(__file__).resolve().parent.parent / "templates",
    )


@pytest.fixture
def store(cfg) -> Store:
    s = Store(cfg.db_path)
    yield s
    s.close()


@pytest.fixture
def sources() -> list[FakeSource]:
    return [FakeSource(url="https://news.example.com/a"),
            FakeSource(url="https://news.example.com/b", publisher="Other News")]
