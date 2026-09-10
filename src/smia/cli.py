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


def _cmd_seed_tenant(args: argparse.Namespace) -> int:
    """Dev-only: create a prospect tenant from platform:handle pairs (no verification)."""
    from smia.db.models import Target, Tenant
    from smia.db.session import db_session

    with db_session() as s:
        from sqlalchemy import select

        tenant = s.execute(select(Tenant).where(Tenant.name == args.name)).scalar_one_or_none()
        if tenant is None:
            tenant = Tenant(name=args.name, industry=args.industry, status="prospect")
            s.add(tenant)
            s.flush()
        existing = {(t.platform, t.handle) for t in tenant.targets}
        added = 0
        for spec in args.targets:
            platform, _, handle = spec.partition(":")
            if platform not in ("instagram", "tiktok", "twitter") or not handle:
                print(f"skip {spec!r}: expected platform:handle")
                continue
            if (platform, handle) in existing:
                continue
            s.add(Target(tenant_id=tenant.id, platform=platform, handle=handle.lstrip("@")))
            added += 1
        print(f"tenant {tenant.name} ({tenant.status}) id={tenant.id}  targets added: {added}")
    return 0


def _cmd_collect(args: argparse.Namespace) -> int:
    from sqlalchemy import select

    from smia.db.models import Tenant
    from smia.db.session import db_session
    from smia.pipeline import collect

    with db_session() as s:
        tenant = s.execute(select(Tenant).where(Tenant.name == args.tenant)).scalar_one_or_none()
        if tenant is None:
            print(f"no tenant named {args.tenant!r}")
            return 1
        summary = collect(s, tenant.id)
    for o in summary.outcomes:
        flag = "CANARY" if o.canary else ("ERROR " + o.error if o.error else "ok")
        print(f"  {o.platform:9s} @{o.handle:24s} posts={o.posts:3d} skipped={o.skipped} credits={o.credits} {flag}")
        if o.caveat:
            print(f"            note: {o.caveat}")
    print(f"total posts upserted: {summary.posts}  credits used: {summary.credits_used}  run id: {summary.pipeline_run_id}")
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

    seed = sub.add_parser("seed-tenant", help="dev: create a prospect tenant from platform:handle pairs")
    seed.add_argument("name")
    seed.add_argument("industry")
    seed.add_argument("targets", nargs="+", metavar="platform:handle")
    seed.set_defaults(func=_cmd_seed_tenant)

    col = sub.add_parser("collect", help="collect all active targets for a tenant (idempotent)")
    col.add_argument("--tenant", required=True, help="tenant name")
    col.set_defaults(func=_cmd_collect)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
