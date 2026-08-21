"""Auto-pruning, with a trigger you can state out loud.

"Stale" is not self-defining, so it is defined here as a conjunction:

    the article's trend has not reappeared in the Trends feed for
    `stale_trend_days`   AND   the article is at least `prune_min_age_days` old

Both conditions are required, and each exists to stop a specific wrong
deletion. Without the age floor, a trend that is quiet for a fortnight would
take a fresh article down with it. Without the trend-silence condition, a
still-trending topic would be pruned purely for having been published a while
ago. Recency alone is not staleness; a topic nobody is searching for any more
is.

Pruning sets `pruned_at` rather than deleting the row, so a prune is
reversible and the next publish simply stops emitting the page.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

log = logging.getLogger(__name__)


def _as_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None


def is_stale(article, trend_last_seen: str | None, cfg,
             now: date | None = None) -> tuple[bool, str]:
    """Apply the trigger. Returns (stale, human-readable reason)."""
    now = now or datetime.now(timezone.utc).date()

    published = _as_date(article["published_at"])
    if published is None:
        return False, "not published"

    age_days = (now - published).days
    if age_days < cfg.prune_min_age_days:
        return False, f"only {age_days}d old (floor {cfg.prune_min_age_days}d)"

    last_seen = _as_date(trend_last_seen)
    if last_seen is None:
        # Never recorded in the feed at all -- age alone decides.
        return True, f"trend never seen in feed; article {age_days}d old"

    quiet_days = (now - last_seen).days
    if quiet_days < cfg.stale_trend_days:
        return False, f"trend seen {quiet_days}d ago (threshold {cfg.stale_trend_days}d)"

    return True, (f"trend quiet {quiet_days}d (>= {cfg.stale_trend_days}d) "
                  f"and article {age_days}d old (>= {cfg.prune_min_age_days}d)")


def prune(store, cfg, now: date | None = None, dry_run: bool = False) -> dict:
    """Mark every stale article. Caller republishes to remove the pages."""
    pruned, kept = [], []
    for article in store.published():
        last_seen = store.trend_last_seen(article["trend_slug"])
        stale, reason = is_stale(article, last_seen, cfg, now=now)
        if stale:
            pruned.append({"slug": article["slug"], "reason": reason})
            if not dry_run:
                store.mark_pruned(article["id"], f"pruned: {reason}")
                log.info("pruned %s (%s)", article["slug"], reason)
        else:
            kept.append({"slug": article["slug"], "reason": reason})

    return {"pruned": len(pruned), "kept": len(kept),
            "pruned_detail": pruned, "dry_run": dry_run}
