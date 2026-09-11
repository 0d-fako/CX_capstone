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
from smia.db.models import AgentRun, ReviewFeedback
from smia.db.session import db_session
from smia.llm import get_client
from smia.settings import get_settings, load_thresholds
from smia.tools.analysis import build_analysis_tools

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
    validator: Callable[[str, RunContext], dict[str, Any]] | None = None,
) -> RunResult:
    """Run the analyst for a digest or playbook. Returns the draft and the trace."""
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
            output_config={"effort": "high"},
            messages=[{"role": "user", "content": brief}],
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
    validation = None
    if validator and status == "ok":
        validation = validator(draft, ctx)
        if not validation.get("ok", True):
            status = "ungrounded"

    with db_session() as s:
        run = s.get(AgentRun, run_id)
        run.finished_at = datetime.now(UTC)
        run.tool_calls = ctx.as_list()
        run.draft = draft
        run.usage = usage
        run.validation = validation
        run.status = status

    return RunResult(run_id=run_id, kind=kind, draft=draft, tool_calls=ctx.as_list(), usage=usage,
                     status=status, validation=validation, messages=messages)
