"""The collect stage. Runs nightly later; runs from the CLI and from `collect_now` today."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from smia.collectors.base import Collector, TargetSpec
from smia.db.models import PipelineRun, Target, Tenant
from smia.ingestion.normalize import ingest

log = logging.getLogger(__name__)

PLATFORM_CAVEATS = {
    "twitter": "vendor returns the ~100 most popular tweets, not the latest; cadence unreliable",
}


@dataclass
class TargetOutcome:
    platform: str
    handle: str
    posts: int = 0
    skipped: int = 0
    credits: int = 0
    canary: bool = False
    error: str | None = None
    caveat: str | None = None


@dataclass
class CollectSummary:
    tenant: str
    outcomes: list[TargetOutcome] = field(default_factory=list)
    credits_used: int = 0
    pipeline_run_id: int | None = None

    @property
    def posts(self) -> int:
        return sum(o.posts for o in self.outcomes)

    @property
    def canaries(self) -> list[TargetOutcome]:
        return [o for o in self.outcomes if o.canary]

    def as_detail(self) -> dict[str, Any]:
        return {"targets": [o.__dict__ for o in self.outcomes]}


def default_collector() -> Collector:
    from smia.collectors.scrapecreators import ScrapeCreatorsCollector
    from smia.settings import get_settings

    key = get_settings().scrapecreators_api_key
    if not key:
        raise RuntimeError("SCRAPECREATORS_API_KEY is not set")
    return ScrapeCreatorsCollector(key)


def collect(
    session: Session,
    tenant_id: uuid.UUID,
    collector: Collector | None = None,
    *,
    credit_budget: int | None = None,
) -> CollectSummary:
    """Collect every active target for a tenant. Idempotent; safe to rerun.

    credit_budget: stop before a target would exceed this many credits (research sessions).
    """
    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        raise LookupError(f"tenant {tenant_id} not found")
    collector = collector or default_collector()
    summary = CollectSummary(tenant=tenant.name)

    run = PipelineRun(stage="collect", tenant_id=tenant_id)
    session.add(run)
    session.flush()
    summary.pipeline_run_id = run.id

    targets = session.execute(
        select(Target).where(Target.tenant_id == tenant_id, Target.active.is_(True))
    ).scalars().all()

    for t in targets:
        out = TargetOutcome(platform=t.platform, handle=t.handle, caveat=PLATFORM_CAVEATS.get(t.platform))
        summary.outcomes.append(out)
        if credit_budget is not None and summary.credits_used + 1 > credit_budget:
            out.error = "credit budget reached; skipped"
            continue
        try:
            res = collector.collect(TargetSpec(t.platform, t.handle))  # type: ignore[arg-type]
        except Exception as e:  # one target failing never stops the run
            log.exception("collect failed for %s/%s", t.platform, t.handle)
            out.error = str(e)[:300]
            continue
        out.credits = res.credits_used
        out.skipped = res.skipped_items
        summary.credits_used += res.credits_used
        if not res.captures:
            out.canary = True  # silence from an active account is itself a signal
            continue
        stats = ingest(session, tenant_id, t, res.captures)
        out.posts = stats.posts_upserted
        t.last_checked_at = datetime.now(timezone.utc)

    run.finished_at = datetime.now(timezone.utc)
    run.items = summary.posts
    run.credits_used = summary.credits_used
    run.status = "canary" if summary.canaries else ("error" if all(o.error for o in summary.outcomes) and summary.outcomes else "ok")
    run.detail = summary.as_detail()
    if summary.canaries:
        run.error = "no posts returned for: " + ", ".join(f"{o.platform}/{o.handle}" for o in summary.canaries)
    return summary
