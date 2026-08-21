"""Command line entry point: `depot run | status | prune | publish`."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from . import prune as prune_mod
from . import publish as publish_mod
from .config import load
from .store import Store


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S")
    # -v is for our stages. The SDK's DEBUG stream dumps every full request
    # body, which buries the pipeline's own log in prompt text.
    for noisy in ("anthropic", "httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def cmd_run(args, cfg) -> int:
    from .run import run
    stats = run(cfg, limit=args.limit, skip_prune=args.no_prune)
    print(json.dumps(stats, indent=2, default=str))
    return 1 if stats.get("error") else 0


def cmd_status(args, cfg) -> int:
    store = Store(cfg.db_path)
    try:
        counts = dict(store.conn.execute(
            "SELECT state, COUNT(*) FROM articles GROUP BY state").fetchall())
        live = store.published()
        pruned = store.conn.execute(
            "SELECT COUNT(*) FROM articles WHERE pruned_at IS NOT NULL").fetchone()[0]
        last = store.conn.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()

        print(f"database   : {cfg.db_path}")
        print(f"states     : {counts or '{}'}")
        print(f"live pages : {len(live)}")
        print(f"pruned     : {pruned}")
        if last:
            print(f"last run   : {last['started_at']} -> "
                  f"{last['finished_at'] or 'unfinished'}")
            if last["stats_json"]:
                print(f"             {last['stats_json']}")
        if args.verbose and live:
            print("\nlive articles:")
            for a in live:
                print(f"  {a['published_at'][:10]}  {a['slug']}")
        return 0
    finally:
        store.close()


def cmd_prune(args, cfg) -> int:
    store = Store(cfg.db_path)
    try:
        result = prune_mod.prune(store, cfg, dry_run=args.dry_run)
        print(json.dumps(result, indent=2))
        if not args.dry_run and result["pruned"]:
            print(json.dumps(publish_mod.publish_site(store, cfg), indent=2))
        return 0
    finally:
        store.close()


def cmd_publish(args, cfg) -> int:
    store = Store(cfg.db_path)
    try:
        print(json.dumps(publish_mod.publish_site(store, cfg), indent=2))
        return 0
    finally:
        store.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="depot", description="The Article Depot pipeline.")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="one full unattended cycle")
    run_p.add_argument("--limit", type=int, default=None,
                       help="max trends this run (default: config)")
    run_p.add_argument("--no-prune", action="store_true",
                       help="skip the prune stage")
    run_p.set_defaults(func=cmd_run)

    status_p = sub.add_parser("status", help="what the pipeline has done")
    status_p.set_defaults(func=cmd_status)

    prune_p = sub.add_parser("prune", help="apply the staleness trigger")
    prune_p.add_argument("--dry-run", action="store_true",
                         help="report what would be pruned, change nothing")
    prune_p.set_defaults(func=cmd_prune)

    pub_p = sub.add_parser("publish", help="rebuild the site from the database")
    pub_p.set_defaults(func=cmd_publish)

    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    return args.func(args, load())


if __name__ == "__main__":
    sys.exit(main())
