"""Every tunable the pipeline has, in one place.

Values come from the environment so a scheduled run can be reconfigured
without a code change. The defaults are what an unattended daily run uses.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


@dataclass(frozen=True)
class Config:
    # --- where things live -------------------------------------------------
    db_path: Path = field(default_factory=lambda: Path(
        os.environ.get("DEPOT_DB", REPO_ROOT / "depot.db")))
    site_dir: Path = field(default_factory=lambda: Path(
        os.environ.get("DEPOT_SITE", REPO_ROOT / "site")))
    template_dir: Path = field(default_factory=lambda: REPO_ROOT / "templates")

    # Shown on the contact page. Set DEPOT_CONTACT_EMAIL before publishing --
    # the default is deliberately not a real address.
    contact_email: str = os.environ.get(
        "DEPOT_CONTACT_EMAIL", "corrections@example.com")

    # --- trend ingestion ---------------------------------------------------
    geo: str = os.environ.get("DEPOT_GEO", "US")
    trends_url: str = os.environ.get(
        "DEPOT_TRENDS_URL", "https://trends.google.com/trending/rss")
    max_trends_per_run: int = _int("DEPOT_MAX_TRENDS", 5)

    # --- polite fetching ---------------------------------------------------
    # A real, contactable UA is the difference between a crawler that gets
    # tolerated and one that gets IP-banned. Point it at a page a site owner
    # can actually read.
    user_agent: str = os.environ.get(
        "DEPOT_UA",
        "ArticleDepotBot/1.0 (+https://github.com/the-abraar/The-Article-Depot)")
    # Floor on the gap between two requests to the same host. robots.txt
    # Crawl-delay overrides this upward, never downward.
    default_crawl_delay: float = _float("DEPOT_CRAWL_DELAY", 2.0)
    request_timeout: float = _float("DEPOT_TIMEOUT", 20.0)
    max_retries: int = _int("DEPOT_MAX_RETRIES", 3)
    # Consecutive 429/403s from one host before we stop touching it this run.
    host_block_threshold: int = _int("DEPOT_BLOCK_THRESHOLD", 3)
    max_source_bytes: int = _int("DEPOT_MAX_SOURCE_BYTES", 2_000_000)

    # --- grounding ---------------------------------------------------------
    # Below this many usable sources a trend is skipped rather than written
    # from the model's own memory. This is the core anti-hallucination lever.
    min_sources: int = _int("DEPOT_MIN_SOURCES", 2)

    # --- generation --------------------------------------------------------
    model: str = os.environ.get("DEPOT_MODEL", "claude-opus-5")
    auditor_model: str = os.environ.get("DEPOT_AUDITOR_MODEL", "claude-opus-5")
    max_tokens: int = _int("DEPOT_MAX_TOKENS", 16000)

    # --- quality gate ------------------------------------------------------
    min_words: int = _int("DEPOT_MIN_WORDS", 400)
    max_words: int = _int("DEPOT_MAX_WORDS", 1600)
    min_sections: int = _int("DEPOT_MIN_SECTIONS", 2)
    # Jaccard similarity against any published article that trips dedupe.
    dupe_threshold: float = _float("DEPOT_DUPE_THRESHOLD", 0.6)
    # The auditor scores 1-5 on grounding and clarity; both must clear this.
    min_audit_score: int = _int("DEPOT_MIN_AUDIT_SCORE", 4)

    # --- pruning -----------------------------------------------------------
    # An article is stale only when BOTH hold: its trend has not reappeared
    # in the feed for `stale_trend_days`, AND the article is at least
    # `prune_min_age_days` old. Two conditions, so a still-hot trend is never
    # pruned for being old and a brand-new article is never pruned for a
    # quiet week.
    stale_trend_days: int = _int("DEPOT_STALE_TREND_DAYS", 14)
    prune_min_age_days: int = _int("DEPOT_PRUNE_MIN_AGE_DAYS", 30)

    @property
    def articles_dir(self) -> Path:
        return self.site_dir / "articles"


def load() -> Config:
    return Config()
