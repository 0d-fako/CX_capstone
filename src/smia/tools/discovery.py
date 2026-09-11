"""Discovery tools for research chat (doc 09 §5, §6a): verify handles, create the prospect
tenant behind a user gate, collect under a credit cap, and submit a report through the
validator. Web search and web fetch are server-side tools added by the runner.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from anthropic import beta_tool
from pydantic import BaseModel, Field
from sqlalchemy import select

from smia.agent import session as sess
from smia.agent.trace import RunContext
from smia.agent.validate import validate
from smia.collectors.base import Collector, Platform
from smia.db.models import Report, Target, Tenant
from smia.db.session import db_session
from smia.pipeline import collect as _collect
from smia.settings import load_thresholds

Role = Literal["direct", "adjacent", "aspirational"]


class TargetProposal(BaseModel):
    platform: Platform
    handle: str
    role: Role = "direct"
    why: str = Field(description="one line on why this account belongs in the set")


@dataclass
class Proposal:
    name: str
    industry: str
    geography: str | None
    venture_brief: str
    targets: list[TargetProposal]


@dataclass
class Decision:
    confirmed: bool
    targets: list[TargetProposal] = field(default_factory=list)
    note: str = ""


ApprovalFn = Callable[[Proposal], Decision]
DeliverFn = Callable[[str, str, uuid.UUID], None]  # (kind, markdown, report_id)


def build_discovery_tools(
    ctx: RunContext,
    session_id: uuid.UUID,
    *,
    approval: ApprovalFn,
    collector: Collector,
    deliver: DeliverFn | None = None,
) -> list[Any]:
    cfg = load_thresholds()["research"]
    cap = int(cfg["session_credit_cap"])
    verified: dict[tuple[str, str], dict[str, Any]] = {}

    def _remaining() -> int:
        return cap - sess.credits_used(session_id)

    @beta_tool
    def resolve_handle(platform: Platform, handle: str) -> str:
        """Verify that an account exists on a platform before proposing it. One vendor credit.

        Returns display name, follower and post counts, or not_found. Never propose an account
        that has not been verified with this tool.

        Args:
            platform: instagram, tiktok or twitter.
            handle: the account handle, with or without @.
        """
        handle = handle.strip().lstrip("@")
        key = (platform, handle.lower())
        if sess.load(session_id)["stage"] == "interview":
            sess.set_stage(session_id, "discovering")
        if key in verified:
            return ctx.record("resolve_handle", {"platform": platform, "handle": handle}, {**verified[key], "cached": True})
        if _remaining() <= 0:
            return ctx.record("resolve_handle", {"platform": platform, "handle": handle},
                              {"found": False, "refused": True}, error=f"session credit cap of {cap} reached")
        info = collector.resolve_handle(platform, handle)
        sess.add_credits(session_id, 1)
        if info is None:
            out = {"found": False, "platform": platform, "handle": handle}
        else:
            out = {"found": True, **info.model_dump()}
            verified[key] = out
        out["credits_remaining"] = _remaining()
        return ctx.record("resolve_handle", {"platform": platform, "handle": handle}, out)

    @beta_tool
    def create_prospect(name: str, industry: str, venture_brief: str, targets: list[TargetProposal],
                        geography: str | None = None) -> str:
        """Propose the competitive set to the user and, once they confirm, create the prospect tenant.

        The user sees the proposed accounts as a table and may confirm, remove or add accounts.
        Nothing is created until they confirm. Call this once, after every handle is verified.

        Args:
            name: short tenant name for the venture, e.g. "brewbox-lagos".
            industry: the category, e.g. "subscription coffee".
            venture_brief: the idea as the user described it, verbatim or lightly tidied.
            targets: the proposed accounts with platform, handle, role and why.
            geography: country or city the venture targets, if stated.
        """
        unverified = [t for t in targets if (t.platform, t.handle.lstrip("@").lower()) not in verified]
        if unverified:
            names = ", ".join(f"{t.platform}/@{t.handle}" for t in unverified)
            return ctx.record("create_prospect", {"name": name, "targets": len(targets)},
                              {"created": False}, error=f"verify these with resolve_handle first: {names}")
        sess.set_stage(session_id, "confirming")
        decision = approval(Proposal(name=name, industry=industry, geography=geography,
                                     venture_brief=venture_brief, targets=targets))
        if not decision.confirmed:
            sess.set_stage(session_id, "discovering")
            return ctx.record("create_prospect", {"name": name, "targets": len(targets)},
                              {"created": False, "user_note": decision.note},
                              error="user did not confirm; revise the set per their note and propose again")
        final = decision.targets or targets
        with db_session() as s:
            existing = s.execute(select(Tenant).where(Tenant.name == name)).scalar_one_or_none()
            if existing is not None:
                name = f"{name}-{uuid.uuid4().hex[:4]}"
            tenant = Tenant(name=name, industry=industry, geography=geography, venture_brief=venture_brief,
                            status="prospect", created_from_session_id=session_id)
            s.add(tenant)
            s.flush()
            for t in final:
                info = verified.get((t.platform, t.handle.lstrip("@").lower()), {})
                s.add(Target(tenant_id=tenant.id, platform=t.platform, handle=t.handle.lstrip("@"), role=t.role,
                             follower_count=info.get("follower_count"),
                             verified_at=datetime.now(UTC) if info else None))
            tenant_id = tenant.id
        ctx.tenant_id = tenant_id
        sess.bind_tenant(session_id, tenant_id)
        sess.set_stage(session_id, "collecting")
        out = {"created": True, "tenant_id": str(tenant_id), "name": name,
               "targets": [t.model_dump() for t in final], "user_note": decision.note}
        return ctx.record("create_prospect", {"name": name, "targets": len(final)}, out)

    @beta_tool
    def collect_now() -> str:
        """Collect recent posts for every account in the confirmed set. About one credit per account.

        Run this once after create_prospect. Reports posts per account and flags any account that
        returned nothing. Refuses when the session credit cap is reached.
        """
        if ctx.tenant_id is None:
            return ctx.record("collect_now", {}, {"collected": False}, error="no tenant yet; confirm the set first")
        budget = _remaining()
        if budget <= 0:
            return ctx.record("collect_now", {}, {"collected": False}, error=f"session credit cap of {cap} reached")
        with db_session() as s:
            summary = _collect(s, ctx.tenant_id, collector, credit_budget=budget)
        sess.add_credits(session_id, summary.credits_used)
        sess.set_stage(session_id, "ready")
        out = {
            "collected": True, "posts": summary.posts, "credits_used": summary.credits_used,
            "credits_remaining": _remaining(),
            "accounts": [{"platform": o.platform, "handle": o.handle, "posts": o.posts, "canary": o.canary,
                          "error": o.error, "caveat": o.caveat} for o in summary.outcomes],
        }
        return ctx.record("collect_now", {}, out)

    @beta_tool
    def submit_report(kind: Literal["playbook", "digest"], markdown: str, period: str) -> str:
        """Submit a finished report. It is validated; if it passes it is stored and delivered for
        review and you should tell the user it is ready. If it fails, fix every finding and submit again.

        Args:
            kind: playbook or digest.
            markdown: the full report in the template, with [D]/[I]/[G] markers and [T#] refs.
            period: the period covered, e.g. "2026-06-13 to 2026-09-11 (trailing 90 days)".
        """
        if ctx.tenant_id is None:
            return ctx.record("submit_report", {"kind": kind}, {"accepted": False}, error="no tenant yet")
        rep = validate(markdown, ctx)
        fatal = [f for f in rep.findings if f.rule != "uncited_in_sentence"]
        if not rep.ok:
            out = {"accepted": False, "summary": rep.summary(),
                   "findings": [f"[{f.rule}] {f.location}: {f.detail}" for f in fatal[:40]]}
            return ctx.record("submit_report", {"kind": kind, "chars": len(markdown)}, out)
        with db_session() as s:
            report = Report(run_id=ctx.run_id, tenant_id=ctx.tenant_id, kind=kind, period=period,
                            body=markdown, status="draft")
            s.add(report)
            s.flush()
            report_id = report.id
        if deliver:
            deliver(kind, markdown, report_id)
        out = {"accepted": True, "report_id": str(report_id), "summary": rep.summary(),
               "warnings": len(rep.findings) - len(fatal)}
        return ctx.record("submit_report", {"kind": kind, "chars": len(markdown)}, out)

    return [resolve_handle, create_prospect, collect_now, submit_report]
