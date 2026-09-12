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
        "SLACK_BOT_TOKEN (slack chat)": s.slack_bot_token,
        "SLACK_APP_TOKEN (slack chat)": s.slack_app_token,
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


def _cmd_label(args: argparse.Namespace) -> int:
    """Dev: label a tenant's posts on an ad-hoc dimension (Haiku, cents)."""
    from sqlalchemy import select

    from smia.db.models import Post, Tenant
    from smia.db.session import db_session
    from smia.labeling.label import label_posts

    with db_session() as s:
        tenant = s.execute(select(Tenant).where(Tenant.name == args.tenant)).scalar_one_or_none()
        if tenant is None:
            print(f"no tenant named {args.tenant!r}")
            return 1
        ids = list(s.execute(select(Post.id).where(Post.tenant_id == tenant.id).limit(args.limit)).scalars())
        r = label_posts(s, tenant.id, args.dimension, args.definition, args.labels.split(","), ids)
    print(f"dimension={r.dimension} hash={r.definition_hash} labels={r.labels}")
    print(f"requested={r.requested} already={r.already_labelled} labelled_now={r.labelled_now} batch_api={r.via_batch_api}")
    print(f"counts={r.counts} usage={r.usage} model={r.model_id}")
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    """Run the analyst for a digest or playbook and print the draft."""
    import json

    from sqlalchemy import select

    from smia.agent.run import run_report
    from smia.db.models import Tenant
    from smia.db.session import db_session

    with db_session() as s:
        tenant = s.execute(select(Tenant).where(Tenant.name == args.tenant)).scalar_one_or_none()
        if tenant is None:
            print(f"no tenant named {args.tenant!r}")
            return 1
        tid = tenant.id

    def progress(text: str) -> None:
        if args.verbose:
            print("--- model ---")
            print(text[:1200])
            print()

    res = run_report(args.kind, tid, on_text=progress)
    print(res.draft)
    print()
    print("=" * 70)
    print(f"run id: {res.run_id}   status: {res.status}   tool calls: {len(res.tool_calls)}   report id: {res.report_id}")
    if res.report_id:
        from pathlib import Path

        from smia.export import export_report

        docx, md = export_report(res.report_id, Path("exports"))
        print(f"saved: {docx}")
        print(f"saved: {md}")
    print("tools used:", ", ".join(f"{c['ref']}={c['name']}" for c in res.tool_calls))
    print("usage:", json.dumps(res.usage))
    if res.validation:
        print("validation:", json.dumps(res.validation)[:800])
    return 0 if res.status in ("ok", "ungrounded") else 1


def _cmd_chat(args: argparse.Namespace) -> int:
    from smia.chat_cli import main as chat_main

    return chat_main(args.session, args.user, args.verbose)


def _cmd_review(args: argparse.Namespace) -> int:
    import uuid

    from smia.delivery.review import record_review

    report = record_review(uuid.UUID(args.report_id), args.action, args.notes, args.reviewer)
    print(f"report {report.id} ({report.kind}) -> {report.status}; feedback recorded")
    if args.action == "revise" and args.rerun:
        from smia.agent.run import run_report

        res = run_report(report.kind, report.tenant_id, revision_of=report.body,
                         extra_brief="Reviewer asked for a revision; their notes are above.")
        print(f"revision run {res.run_id} status={res.status}")
        print(res.draft)
    return 0


def _cmd_activate(args: argparse.Namespace) -> int:
    from smia.delivery.review import activate_tenant

    t = activate_tenant(args.tenant)
    print(f"tenant {t.name} is now {t.status}; schedule nightly collect for it")
    return 0


def _cmd_slack(args: argparse.Namespace) -> int:
    from smia.slack_app import main as slack_main

    return slack_main()


def _cmd_export(args: argparse.Namespace) -> int:
    import uuid
    from pathlib import Path

    from smia.export import export_report, latest_report_id

    rid = uuid.UUID(args.report_id) if args.report_id else latest_report_id(args.tenant, args.kind)
    if rid is None:
        print("no report found; pass a report id or --tenant")
        return 1
    docx, md = export_report(rid, Path(args.out))
    print(docx)
    print(md)
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
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

    lab = sub.add_parser("label", help="dev: label a tenant's posts on an ad-hoc dimension")
    lab.add_argument("--tenant", required=True)
    lab.add_argument("--dimension", required=True)
    lab.add_argument("--definition", required=True)
    lab.add_argument("--labels", required=True, help="comma-separated; 'other' is always added")
    lab.add_argument("--limit", type=int, default=200)
    lab.set_defaults(func=_cmd_label)

    for kind in ("digest", "playbook"):
        rp = sub.add_parser(kind, help=f"run the analyst and print a {kind} draft")
        rp.add_argument("--tenant", required=True)
        rp.add_argument("--verbose", action="store_true", help="print the model's text as it goes")
        rp.set_defaults(func=_cmd_report, kind=kind)

    ch = sub.add_parser("chat", help="research chat: idea -> competitors -> playbook (terminal)")
    ch.add_argument("--session", help="resume an existing session id")
    ch.add_argument("--user", default="cli")
    ch.add_argument("--verbose", action="store_true", help="show the analyst's interim text")
    ch.set_defaults(func=_cmd_chat)

    rv = sub.add_parser("review", help="record a reviewer decision on a report")
    rv.add_argument("report_id")
    rv.add_argument("action", choices=["approve", "revise", "user_test"])
    rv.add_argument("--notes")
    rv.add_argument("--reviewer", default="cli")
    rv.add_argument("--rerun", action="store_true", help="on revise, rerun the report with the notes")
    rv.set_defaults(func=_cmd_review)

    ac = sub.add_parser("activate", help="flip a prospect tenant to active")
    ac.add_argument("--tenant", required=True)
    ac.set_defaults(func=_cmd_activate)

    sub.add_parser("slack", help="run the Slack chat surface (Socket Mode; DM the app or mention it)").set_defaults(func=_cmd_slack)

    ex = sub.add_parser("export", help="write a report as .docx and .md (latest for a tenant, or by id)")
    ex.add_argument("report_id", nargs="?")
    ex.add_argument("--tenant")
    ex.add_argument("--kind", choices=["playbook", "digest"])
    ex.add_argument("--out", default="exports")
    ex.set_defaults(func=_cmd_export)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
