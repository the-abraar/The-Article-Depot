"""Durable pipeline state.

Every stage records what it did here, so an interrupted run resumes instead
of restarting and a re-run on the same day is a no-op rather than a
duplicate. The unique key is (trend_slug, run_date): one article per trend
per day, enforced by the schema rather than by a check the caller can forget.

Article rows move through: pending -> generated -> published, or terminate
at rejected (quality gate) / skipped (not enough sources). `pruned_at`
unpublishes without deleting, so a prune is reversible.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS trends (
    slug          TEXT NOT NULL,
    title         TEXT NOT NULL,
    traffic       TEXT,
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    PRIMARY KEY (slug)
);

CREATE TABLE IF NOT EXISTS articles (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    trend_slug    TEXT NOT NULL,
    run_date      TEXT NOT NULL,
    state         TEXT NOT NULL DEFAULT 'pending',
    title         TEXT,
    slug          TEXT,
    body_json     TEXT,
    reason        TEXT,
    audit_json    TEXT,
    created_at    TEXT NOT NULL,
    published_at  TEXT,
    pruned_at     TEXT,
    UNIQUE (trend_slug, run_date)
);

CREATE TABLE IF NOT EXISTS sources (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id    INTEGER NOT NULL REFERENCES articles(id),
    url           TEXT NOT NULL,
    title         TEXT,
    publisher     TEXT,
    text          TEXT NOT NULL,
    fetched_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date      TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    stats_json    TEXT
);

CREATE INDEX IF NOT EXISTS idx_articles_state ON articles(state);
CREATE INDEX IF NOT EXISTS idx_sources_article ON sources(article_id);
"""

TERMINAL_STATES = ("published", "rejected", "skipped")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    # --- trends ------------------------------------------------------------

    def record_trend(self, slug: str, title: str, traffic: str | None,
                     seen_on: str) -> None:
        """Upsert a trend sighting. `last_seen` drives the staleness trigger."""
        with self.tx() as c:
            c.execute(
                """INSERT INTO trends (slug, title, traffic, first_seen, last_seen)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(slug) DO UPDATE SET
                       last_seen = excluded.last_seen,
                       traffic   = COALESCE(excluded.traffic, trends.traffic)""",
                (slug, title, traffic, seen_on, seen_on),
            )

    def trend_last_seen(self, slug: str) -> str | None:
        row = self.conn.execute(
            "SELECT last_seen FROM trends WHERE slug = ?", (slug,)).fetchone()
        return row["last_seen"] if row else None

    # --- articles ----------------------------------------------------------

    def claim_article(self, trend_slug: str, run_date: str) -> int | None:
        """Reserve work for (trend, date). Returns None if already claimed.

        This is the idempotency gate: a second run on the same day hits the
        UNIQUE constraint and gets None, so no duplicate article is written.
        """
        try:
            with self.tx() as c:
                cur = c.execute(
                    """INSERT INTO articles (trend_slug, run_date, state, created_at)
                       VALUES (?, ?, 'pending', ?)""",
                    (trend_slug, run_date, utcnow()),
                )
                return int(cur.lastrowid)
        except sqlite3.IntegrityError:
            return None

    def resumable(self, run_date: str) -> list[sqlite3.Row]:
        """Rows claimed but never finished — what a resume picks back up."""
        return list(self.conn.execute(
            f"""SELECT * FROM articles
                WHERE run_date = ? AND state NOT IN {TERMINAL_STATES}
                ORDER BY id""",
            (run_date,),
        ))

    def get_article(self, article_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()

    def set_state(self, article_id: int, state: str, *,
                  reason: str | None = None) -> None:
        with self.tx() as c:
            c.execute("UPDATE articles SET state = ?, reason = ? WHERE id = ?",
                      (state, reason, article_id))

    def save_draft(self, article_id: int, title: str, slug: str,
                   body: dict[str, Any]) -> None:
        with self.tx() as c:
            c.execute(
                """UPDATE articles
                   SET title = ?, slug = ?, body_json = ?, state = 'generated'
                   WHERE id = ?""",
                (title, slug, json.dumps(body), article_id),
            )

    def save_audit(self, article_id: int, audit: dict[str, Any]) -> None:
        with self.tx() as c:
            c.execute("UPDATE articles SET audit_json = ? WHERE id = ?",
                      (json.dumps(audit), article_id))

    def mark_published(self, article_id: int) -> None:
        with self.tx() as c:
            c.execute(
                """UPDATE articles SET state = 'published', published_at = ?
                   WHERE id = ?""",
                (utcnow(), article_id),
            )

    def mark_pruned(self, article_id: int, reason: str) -> None:
        with self.tx() as c:
            c.execute(
                """UPDATE articles SET pruned_at = ?, reason = ? WHERE id = ?""",
                (utcnow(), reason, article_id),
            )

    def published(self, include_pruned: bool = False) -> list[sqlite3.Row]:
        sql = "SELECT * FROM articles WHERE state = 'published'"
        if not include_pruned:
            sql += " AND pruned_at IS NULL"
        sql += " ORDER BY published_at DESC"
        return list(self.conn.execute(sql))

    def slug_exists(self, slug: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM articles WHERE slug = ? LIMIT 1", (slug,)
        ).fetchone() is not None

    # --- sources -----------------------------------------------------------

    def save_sources(self, article_id: int, sources: list[dict[str, Any]]) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM sources WHERE article_id = ?", (article_id,))
            c.executemany(
                """INSERT INTO sources (article_id, url, title, publisher, text, fetched_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                [(article_id, s["url"], s.get("title"), s.get("publisher"),
                  s["text"], utcnow()) for s in sources],
            )

    def sources_for(self, article_id: int) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT * FROM sources WHERE article_id = ? ORDER BY id", (article_id,)))

    # --- runs --------------------------------------------------------------

    def start_run(self, run_date: str) -> int:
        with self.tx() as c:
            cur = c.execute(
                "INSERT INTO runs (run_date, started_at) VALUES (?, ?)",
                (run_date, utcnow()))
            return int(cur.lastrowid)

    def finish_run(self, run_id: int, stats: dict[str, Any]) -> None:
        with self.tx() as c:
            c.execute(
                "UPDATE runs SET finished_at = ?, stats_json = ? WHERE id = ?",
                (utcnow(), json.dumps(stats), run_id))


def today(cfg_date: date | None = None) -> str:
    return (cfg_date or datetime.now(timezone.utc).date()).isoformat()
