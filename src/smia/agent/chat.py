"""Research chat: one analyst, one session, many turns (doc 09 §6a).

Each turn is an agent_runs row. Refs accumulate across the session so a follow-up can cite
evidence gathered earlier. Conversation history is replayed as text; tool results live in the
trace, not in the replayed messages, which keeps turns cheap and the transcript append-only.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from smia.agent import session as sess
from smia.agent.trace import RunContext
from smia.agent.validate import validate
from smia.collectors.base import Collector
from smia.cost import add_usage, estimate_usd, usage_of
from smia.db.models import AgentRun
from smia.db.session import db_session
from smia.delivery.slack import post_report
from smia.llm import get_client
from smia.settings import get_settings, load_thresholds
from smia.tools.analysis import build_analysis_tools
from smia.tools.discovery import ApprovalFn, DeliverFn, build_discovery_tools

log = logging.getLogger(__name__)

RESEARCH_PROMPT_VERSION = "research_v1"
MAX_TOKENS = 16_000
CONTEXT_BETA = "context-management-2025-06-27"


@dataclass
class TurnResult:
    run_id: uuid.UUID
    reply: str
    tool_names: list[str]
    usage: dict[str, Any]
    validation: dict[str, Any] | None
    stage: str
    status: str
    messages: list[Any] = field(default_factory=list)


def _system() -> list[dict[str, Any]]:
    text = (Path(__file__).parent / "prompts" / f"{RESEARCH_PROMPT_VERSION}.md").read_text(encoding="utf-8")
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


def _text_of(message: Any) -> str:
    return "".join(getattr(b, "text", "") for b in message.content if getattr(b, "type", None) == "text")


class ChatRunner:
    def __init__(
        self,
        session_id: uuid.UUID,
        *,
        approval: ApprovalFn,
        collector: Collector | None = None,
        client: Any = None,
        on_text: Callable[[str], None] | None = None,
        deliver: DeliverFn | None = None,
        deliver_to_slack: bool = True,
    ) -> None:
        self.session_id = session_id
        self.client = client or get_client()
        self.on_text = on_text
        state = sess.load(session_id)
        self.ctx = RunContext(tenant_id=state["tenant_id"], run_id=uuid.uuid4(), kind="research")
        self._rehydrate_trace()
        if collector is None:
            from smia.pipeline import default_collector

            collector = default_collector()
        self.collector = collector
        cfg = load_thresholds()["research"]
        self.web_tools = [
            {"type": "web_search_20260209", "name": "web_search", "max_uses": int(cfg["web_search_max_uses"])},
            {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": int(cfg["web_fetch_max_uses"])},
        ]

        def _webhook(kind: str, markdown: str, report_id: uuid.UUID) -> None:
            if deliver_to_slack:
                try:
                    post_report(kind, markdown, report_id, validation_summary="validated: every number traces to a tool call")
                except Exception:
                    log.exception("slack delivery failed for report %s", report_id)

        deliver = deliver or _webhook

        self.discovery_tools = build_discovery_tools(self.ctx, session_id, approval=approval,
                                                     collector=collector, deliver=deliver)
        self.analysis_tools = build_analysis_tools(self.ctx)

    def _rehydrate_trace(self) -> None:
        """Refs from earlier turns stay citable after a resume."""
        with db_session() as s:
            runs = s.execute(
                select(AgentRun.tool_calls).where(AgentRun.session_id == self.session_id).order_by(AgentRun.started_at)
            ).scalars().all()
        calls: list[dict[str, Any]] = []
        for tc in runs:
            calls.extend(tc or [])
        if calls:
            self.ctx = RunContext.from_stored(self.ctx.tenant_id, self.ctx.run_id, "research", calls)

    def session_spend_usd(self) -> float:
        with db_session() as s:
            rows = s.execute(select(AgentRun.usage).where(AgentRun.session_id == self.session_id)).scalars().all()
        return round(sum(float((u or {}).get("est_usd", 0) or 0) for u in rows), 4)

    def history(self) -> list[dict[str, str]]:
        msgs = sess.load(self.session_id)["messages"]
        out: list[dict[str, str]] = []
        for m in msgs:
            if m["role"] in ("user", "assistant") and m.get("content"):
                if out and out[-1]["role"] == m["role"]:
                    out[-1]["content"] += "\n\n" + m["content"]
                else:
                    out.append({"role": m["role"], "content": m["content"]})
        return out

    def turn(self, user_text: str) -> TurnResult:
        settings = get_settings()
        thresholds = load_thresholds()
        sess.append(self.session_id, "user", user_text)
        with db_session() as s:
            run = AgentRun(session_id=self.session_id, tenant_id=self.ctx.tenant_id, kind="research",
                           model_id=settings.analyst_model, prompt_version=RESEARCH_PROMPT_VERSION,
                           thresholds_version=str(thresholds["version"]))
            s.add(run)
            s.flush()
            run_id = run.id
        self.ctx.run_id = run_id
        n_before = len(self.ctx.calls)

        messages = self.history()
        if not messages or messages[-1]["role"] != "user":
            messages.append({"role": "user", "content": user_text})
        tools: list[Any] = [*self.discovery_tools, *self.analysis_tools, *self.web_tools]

        spend = thresholds.get("spend", {})
        stage_before = sess.load(self.session_id)["stage"]
        effort = spend.get("effort_followup", "low") if stage_before == "ready" else spend.get("effort_research_turn", "medium")
        usd_cap = float(spend.get("session_usd_cap", 0) or 0)
        spent_before = self.session_spend_usd()
        context_management = {
            "edits": [{
                "type": "clear_tool_uses_20250919",
                "trigger": {"type": "input_tokens", "value": int(spend.get("context_clear_trigger_tokens", 50000))},
                "keep": {"type": "tool_uses", "value": int(spend.get("context_keep_tool_uses", 6))},
                "clear_at_least": {"type": "input_tokens", "value": int(spend.get("context_clear_at_least_tokens", 10000))},
                "exclude_tools": ["create_prospect", "collect_now", "submit_report"],
            }]
        }

        out_msgs: list[Any] = []
        status = "ok"
        reply = ""
        usage: dict[str, Any] = {}
        budget_note = ""
        try:
            runner = self.client.beta.messages.tool_runner(
                model=settings.analyst_model, max_tokens=MAX_TOKENS,
                max_iterations=int(spend.get("max_iterations_research", 45)),
                system=_system(), tools=tools, thinking={"type": "adaptive"},
                output_config={"effort": effort}, messages=messages,
                cache_control={"type": "ephemeral"},          # cache the whole prefix each iteration
                context_management=context_management,        # drop stale tool results past the trigger
                betas=[CONTEXT_BETA],
            )
            for m in runner:
                out_msgs.append(m)
                add_usage(usage, usage_of(m))
                usage["est_usd"] = estimate_usd(usage, settings.analyst_model)
                t = _text_of(m)
                if t and self.on_text:
                    self.on_text(t)
                if m.stop_reason == "refusal":
                    status = "error"
                    reply = "I can't help with that part of the request."
                    break
                if usd_cap and spent_before + usage["est_usd"] > usd_cap and m.stop_reason == "tool_use":
                    budget_note = (
                        f"Stopped here to respect the session budget (about ${usd_cap:.2f}; "
                        f"this session is at ${spent_before + usage['est_usd']:.2f}). "
                        "Ask a narrower question, or raise session_usd_cap in config/thresholds.yaml."
                    )
                    status = "budget"
                    break
            if status in ("ok", "budget"):
                reply = _text_of(out_msgs[-1]) if out_msgs else ""
                if budget_note:
                    reply = (reply + chr(10) + chr(10) if reply else "") + budget_note
        except Exception as e:
            log.exception("chat turn %s failed", run_id)
            status = "error"
            reply = reply or f"Something went wrong on my side: {e}"

        new_calls = self.ctx.calls[n_before:]
        validation = None
        if status == "ok" and reply:
            rep = validate(reply, self.ctx, mode="light")
            validation = rep.as_dict()
            if not rep.ok:
                status = "ungrounded"
                reply += f"\n\n_(validator: {rep.numbers_unbacked} number(s) in this reply could not be traced to a tool result)_"

        sess.append(self.session_id, "assistant", reply, run_id=str(run_id))
        stage = sess.load(self.session_id)["stage"]
        usage.setdefault("est_usd", estimate_usd(usage, settings.analyst_model))
        usage["effort"] = effort
        with db_session() as s:
            run = s.get(AgentRun, run_id)
            run.tenant_id = self.ctx.tenant_id
            run.finished_at = datetime.now(UTC)
            run.tool_calls = [c.as_dict() for c in new_calls]
            run.draft = reply
            run.usage = usage
            run.validation = validation
            run.status = status
        return TurnResult(run_id=run_id, reply=reply, tool_names=[c.name for c in new_calls], usage=usage,
                          validation=validation, stage=stage, status=status, messages=out_msgs)
