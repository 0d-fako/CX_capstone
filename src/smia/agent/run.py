"""One agent run: brief -> tool loop -> draft, fully traced to agent_runs.

The loop is the Anthropic SDK's tool runner; the tools, the trace and the brief are ours.
No framework. Validation (step 8) plugs in between draft and status.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from smia.agent.brief import (
    ANALYST_PROMPT_VERSION,
    load_tenant,
    period_for,
    system_blocks,
    user_turn,
)
from smia.agent.trace import RunContext
from smia.agent.validate import ValidationReport, validate
from smia.cost import estimate_usd
from smia.db.models import AgentRun, Report, ReviewFeedback
from smia.db.session import db_session
from smia.llm import get_client
from smia.settings import get_settings, load_thresholds
from smia.tools.analysis import build_analysis_tools, tenant_summary

log = logging.getLogger(__name__)

MAX_ITERATIONS = 60
MAX_TOKENS = 16_000


@dataclass
class RunResult:
    run_id: uuid.UUID
    kind: str
    draft: str
    tool_calls: list[dict[str, Any]]
    usage: dict[str, Any]
    status: str
    validation: dict[str, Any] | None = None
    messages: list[Any] = field(default_factory=list)
    report_id: uuid.UUID | None = None


def _sum_usage(messages: list[Any]) -> dict[str, Any]:
    tot = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
           "cache_creation_input_tokens": 0, "turns": 0}
    for m in messages:
        u = getattr(m, "usage", None)
        if not u:
            continue
        tot["turns"] += 1
        for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
            tot[k] += int(getattr(u, k, 0) or 0)
    return tot


def _text_of(message: Any) -> str:
    return "".join(getattr(b, "text", "") for b in message.content if getattr(b, "type", None) == "text")


def _recent_reviewer_notes(tenant_id: uuid.UUID, limit: int = 5) -> list[str]:
    from sqlalchemy import select

    with db_session() as s:
        rows = s.execute(
            select(ReviewFeedback.notes).where(ReviewFeedback.tenant_id == tenant_id, ReviewFeedback.notes.isnot(None))
            .order_by(ReviewFeedback.created_at.desc()).limit(limit)
        ).scalars().all()
    return [r for r in rows if r]


def run_report(
    kind: str,
    tenant_id: uuid.UUID,
    *,
    revision_of: str | None = None,
    extra_brief: str | None = None,
    client: Any = None,
    on_text: Callable[[str], None] | None = None,
    auto_revise: bool = True,
    validator: Callable[[str, RunContext], ValidationReport] = validate,
) -> RunResult:
    """Run the analyst for a digest or playbook, validate, revise once on failure.

    Returns the final draft and trace. A draft that fails validation twice is returned with
    status "ungrounded" so a reviewer sees it flagged rather than silently dropped."""
    if kind not in ("digest", "playbook"):
        raise ValueError("kind must be digest or playbook")
    settings = get_settings()
    thresholds = load_thresholds()
    client = client or get_client()

    with db_session() as s:
        tenant = load_tenant(s, tenant_id)
        run = AgentRun(
            tenant_id=tenant_id, kind=kind, model_id=settings.analyst_model,
            prompt_version=ANALYST_PROMPT_VERSION, thresholds_version=str(thresholds["version"]),
        )
        s.add(run)
        s.flush()
        run_id = run.id
        brief = user_turn(
            tenant, kind, period=period_for(kind),
            reviewer_notes=_recent_reviewer_notes(tenant_id), revision_of=revision_of, extra=extra_brief,
        )

    ctx = RunContext(tenant_id=tenant_id, run_id=run_id, kind=kind)
    ctx.record("brief", {}, tenant_summary(tenant_id))  # T1: the header facts are citable too
    tools: list[Any] = build_analysis_tools(ctx)
    tools.append({
        "type": "web_search_20260209", "name": "web_search",
        "max_uses": int(thresholds["scheduled"]["web_search_max_uses"]),
    })

    messages: list[Any] = []
    draft = ""
    status = "ok"
    try:
        runner = client.beta.messages.tool_runner(
            model=settings.analyst_model,
            max_tokens=MAX_TOKENS,
            max_iterations=MAX_ITERATIONS,
            system=system_blocks(),
            tools=tools,
            thinking={"type": "adaptive"},
            output_config={"effort": thresholds.get("spend", {}).get("effort_scheduled", "high")},
            messages=[{"role": "user", "content": brief}],
            cache_control={"type": "ephemeral"},
        )
        for message in runner:
            messages.append(message)
            text = _text_of(message)
            if text and on_text:
                on_text(text)
            if message.stop_reason == "refusal":
                status = "error"
                draft = f"[model refused: {getattr(message, 'stop_details', None)}]"
                break
        if status == "ok":
            draft = _text_of(messages[-1]) if messages else ""
    except Exception as e:
        log.exception("agent run %s failed", run_id)
        status = "error"
        draft = draft or f"[run failed: {e}]"

    usage = _sum_usage(messages)
    usage["est_usd"] = estimate_usd(usage, settings.analyst_model)
    validation: dict[str, Any] | None = None
    report: ValidationReport | None = None
    if status == "ok":
        report = validator(draft, ctx)
        validation = report.as_dict()
        if not report.ok:
            status = "ungrounded"

    with db_session() as s:
        run = s.get(AgentRun, run_id)
        run.finished_at = datetime.now(UTC)
        run.tool_calls = ctx.as_list()
        run.draft = draft
        run.usage = usage
        run.validation = validation
        run.status = status

    report_id: uuid.UUID | None = None
    if status in ("ok", "ungrounded") and draft and not (status == "ungrounded" and auto_revise):
        # store the deliverable (a first-pass ungrounded draft is not stored; its revision will be)
        with db_session() as s:
            rep_row = Report(run_id=run_id, tenant_id=tenant_id, kind=kind, period=period_for(kind),
                             body=draft, status="draft")
            s.add(rep_row)
            s.flush()
            report_id = rep_row.id
        try:
            from smia.delivery.slack import post_report

            summary = "validated: every number traces to a tool call" if status == "ok" else                 "VALIDATOR FLAGGED THIS DRAFT: " + (report.summary() if report else "")
            post_report(kind, draft, report_id, validation_summary=summary)
        except Exception:
            log.exception("slack delivery failed")

    result = RunResult(run_id=run_id, kind=kind, draft=draft, tool_calls=ctx.as_list(), usage=usage,
                       status=status, validation=validation, messages=messages, report_id=report_id)

    if status == "ungrounded" and auto_revise and report is not None:
        log.warning("run %s failed validation; revising once", run_id)
        fatal = [f for f in report.findings if f.rule != "uncited_in_sentence"]
        notes = chr(10).join(f"- [{f.rule}] {f.location}: {f.detail}" for f in fatal[:40])
        extra = (
            "An automated validator rejected the previous draft. Fix every item below; re-run the "
            "tools you need and cite a ref on every number. Findings:" + chr(10) + notes
        )
        second = run_report(kind, tenant_id, revision_of=draft, extra_brief=extra, client=client,
                            on_text=on_text, auto_revise=False, validator=validator)
        with db_session() as s:
            first = s.get(AgentRun, run_id)
            first.status = "ungrounded_revised"
        return second

    return result
