"""Builds the stable system prompt (cached) and the per-run user turn (varies)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from smia.db.models import Tenant
from smia.tools.analysis import tenant_summary

PROMPTS = Path(__file__).parent / "prompts"
ANALYST_PROMPT_VERSION = "analyst_v1"


def system_blocks(prompt_version: str = ANALYST_PROMPT_VERSION) -> list[dict[str, Any]]:
    text = (PROMPTS / f"{prompt_version}.md").read_text(encoding="utf-8")
    # One cache breakpoint at the end of the stable prefix (tools render before system).
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


def period_for(kind: str, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    if kind == "digest":
        start = (now - timedelta(days=7)).date()
        return f"{start.isoformat()} to {now.date().isoformat()}"
    start = (now - timedelta(days=90)).date()
    return f"{start.isoformat()} to {now.date().isoformat()} (trailing 90 days)"


def user_turn(tenant: Tenant, kind: str, *, period: str, reviewer_notes: list[str] | None = None,
              revision_of: str | None = None, extra: str | None = None) -> str:
    summary = tenant_summary(tenant.id)
    targets = "\n".join(
        f"- {t['platform']}: @{t['handle']} ({t['role']}, {t['ownership']})" for t in summary["targets"]
    ) or "- (none)"
    lines = [
        f"# Brief: {kind}",
        "",
        f"Tenant: {tenant.name}",
        f"Industry: {tenant.industry}",
        f"Geography: {tenant.geography or 'not specified'}",
        f"Status: {tenant.status}",
        f"Period: {period}",
        f"Generated: {datetime.now(UTC).date().isoformat()}",
        "",
        "Venture brief (as the user described it):",
        tenant.venture_brief or "(none recorded)",
        "",
        "Accounts tracked:",
        targets,
        "",
        (
            f"Posts on record: {summary['posts']} (posted {summary['earliest_post']} to "
            f"{summary['latest_post']}); days of snapshots so far: {summary['snapshot_days']}."
        ),
    ]
    if summary["snapshot_days"] < 7:
        lines.append(
            "Note: fewer than 7 days of snapshots exist, so every RE is still 'initial' and no trend "
            "windows are populated. Say so once in the header and do not over-read the numbers."
        )
    if reviewer_notes:
        lines += ["", "Reviewer notes to address (most recent first):"] + [f"- {n}" for n in reviewer_notes]
    if revision_of:
        lines += ["", "This is a revision. The previous draft follows; keep what was right, fix what the notes say:",
                  "<previous_draft>", revision_of, "</previous_draft>"]
    if extra:
        lines += ["", extra]
    lines += [
        "",
        f"Produce the {kind} per the template. Investigate with the tools first. Cite a ref for every number.",
    ]
    return "\n".join(lines)


def load_tenant(session: Any, tenant_id: uuid.UUID) -> Tenant:
    t = session.get(Tenant, tenant_id)
    if t is None:
        raise LookupError(f"tenant {tenant_id} not found")
    return t
