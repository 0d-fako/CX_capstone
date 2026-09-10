"""Entry points. Subcommands are added as the build progresses (doc 10)."""

from __future__ import annotations

import argparse
import sys


def _cmd_config(args: argparse.Namespace) -> int:
    from smia.settings import get_settings, load_dimensions, load_thresholds

    s = get_settings()
    t = load_thresholds()
    d = load_dimensions()
    print(f"database: {s.database_url.split('@')[-1]}")
    print(f"analyst model: {s.analyst_model}   labeling model: {s.labeling_model}")
    print(f"thresholds v{t['version']}   promoted dimensions: {', '.join(d) or '(none)'}")
    missing = [k for k, v in {
        "ANTHROPIC_API_KEY": s.anthropic_api_key,
        "SCRAPECREATORS_API_KEY": s.scrapecreators_api_key,
        "SLACK_WEBHOOK_URL": s.slack_webhook_url,
    }.items() if not v]
    print(f"missing keys: {', '.join(missing) or 'none'}")
    return 0


def _cmd_db_check(args: argparse.Namespace) -> int:
    from sqlalchemy import inspect, text

    from smia.db.models import ALL_TABLES
    from smia.db.session import get_engine

    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("select 1"))
        present = set(inspect(conn).get_table_names())
    expected = {t.__tablename__ for t in ALL_TABLES}
    missing = sorted(expected - present)
    print(f"connected. tables present: {len(present & expected)}/{len(expected)}")
    if missing:
        print(f"missing: {', '.join(missing)}  (run: uv run alembic upgrade head)")
        return 1
    return 0


def _cmd_smoke(args: argparse.Namespace) -> int:
    """Resolve and fetch one real handle. Costs two vendor credits."""
    from smia.collectors.base import TargetSpec
    from smia.collectors.scrapecreators import ScrapeCreatorsCollector
    from smia.settings import get_settings

    key = get_settings().scrapecreators_api_key
    if not key:
        print("SCRAPECREATORS_API_KEY is not set")
        return 1
    c = ScrapeCreatorsCollector(key)
    info = c.resolve_handle(args.platform, args.handle)
    if info is None:
        print(f"{args.platform}/{args.handle}: not found (credits used: {c.credits_used})")
        return 1
    print("profile:", info.model_dump())
    res = c.collect(TargetSpec(args.platform, args.handle))
    print(f"posts returned: {len(res.captures)}  skipped: {res.skipped_items}  credits used: {c.credits_used}")
    if res.captures:
        first = res.captures[0]
        print("first capture:", first.model_dump(exclude={"raw_json"}))
        from collections import Counter
        empties = Counter(f for cap in res.captures for f in cap.empty_fields())
        print("empty fields across batch:", dict(empties) or "none")
        if args.raw:
            import json
            print(json.dumps(first.raw_json, indent=2)[:3000])
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="smia", description="Social Media Intelligence Agent")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("config", help="show resolved settings and which keys are missing").set_defaults(
        func=_cmd_config
    )
    sub.add_parser("db-check", help="connect to the database and verify the schema").set_defaults(
        func=_cmd_db_check
    )

    smoke = sub.add_parser("smoke", help="resolve + fetch one real handle (2 credits)")
    smoke.add_argument("platform", choices=["instagram", "tiktok", "twitter"])
    smoke.add_argument("handle")
    smoke.add_argument("--raw", action="store_true", help="print the vendor JSON of the first item")
    smoke.set_defaults(func=_cmd_smoke)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
