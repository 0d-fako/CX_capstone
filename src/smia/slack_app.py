"""Slack chat surface (doc 09 §8): Socket Mode bot. DM the app or mention it in a channel;
each thread is one research session. The proposed competitive set arrives with Confirm and
Decline buttons; the playbook posts into the thread. No public URL needed.
"""

from __future__ import annotations

import logging
import re
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from smia.agent import session as sess
from smia.agent.chat import ChatRunner
from smia.delivery.slack import _mrkdwn, post_report
from smia.settings import get_settings
from smia.tools.discovery import Decision, Proposal

log = logging.getLogger(__name__)

CHUNK = 3500
APPROVAL_TIMEOUT_S = 15 * 60


@dataclass
class Pending:
    """A proposal waiting for a button click (or a text reply) in one thread."""

    proposal: Proposal
    event: threading.Event = field(default_factory=threading.Event)
    decision: Decision | None = None


@dataclass
class ThreadState:
    session_id: uuid.UUID
    runner: ChatRunner
    lock: threading.Lock = field(default_factory=threading.Lock)
    pending: Pending | None = None


class SlackSurface:
    def __init__(self, bot_token: str, app_token: str) -> None:
        self.app = App(token=bot_token, logger=log)
        self.app_token = app_token
        self.threads: dict[str, ThreadState] = {}
        self.threads_lock = threading.Lock()
        self.bot_user_id: str | None = None
        self._register()

    # ------------------------------------------------------------------ helpers

    def _post(self, channel: str, thread_ts: str, text: str, blocks: list[dict[str, Any]] | None = None) -> str | None:
        ts = None
        for i in range(0, max(1, len(text)), CHUNK):
            chunk = text[i : i + CHUNK]
            kwargs: dict[str, Any] = {"channel": channel, "thread_ts": thread_ts, "text": chunk}
            if blocks and i == 0:
                kwargs["blocks"] = blocks
            r = self.app.client.chat_postMessage(**kwargs)
            ts = ts or r.get("ts")
        return ts

    def _state_for(self, channel: str, thread_ts: str, user: str) -> ThreadState:
        key = f"slack:{channel}:{thread_ts}"
        with self.threads_lock:
            st = self.threads.get(key)
            if st is None:
                sid = sess.find_by_channel(key) or sess.create(user, channel=key)
                st = ThreadState(session_id=sid, runner=self._runner(sid, channel, thread_ts))
                self.threads[key] = st
            return st

    def _runner(self, sid: uuid.UUID, channel: str, thread_ts: str) -> ChatRunner:
        key = f"slack:{channel}:{thread_ts}"

        def approval(p: Proposal) -> Decision:
            st = self.threads[key]
            st.pending = Pending(proposal=p)
            self._post(channel, thread_ts, self._proposal_text(p), blocks=self._proposal_blocks(p))
            if not st.pending.event.wait(APPROVAL_TIMEOUT_S):
                st.pending = None
                return Decision(confirmed=False, note="no answer within 15 minutes")
            d = st.pending.decision or Decision(confirmed=False, note="no decision")
            st.pending = None
            return d

        def on_text(text: str) -> None:
            # the analyst's interim narration between tool calls, kept short
            snippet = text.strip()
            if snippet and len(snippet) < 1500:
                try:
                    self._post(channel, thread_ts, _mrkdwn(snippet))
                except Exception:
                    log.exception("interim post failed")

        def deliver(kind: str, markdown: str, report_id: uuid.UUID) -> None:
            head = (
                f"*{kind.title()} ready* · report `{report_id}` · "
                "validated: every number traces to a tool call"
            )
            self._post(channel, thread_ts, head + "\n\n" + _mrkdwn(markdown))
            try:
                post_report(kind, markdown, report_id, validation_summary="validated: every number traces to a tool call")
            except Exception:
                log.exception("webhook delivery failed")

        return ChatRunner(sid, approval=approval, on_text=on_text, deliver=deliver)

    @staticmethod
    def _proposal_text(p: Proposal) -> str:
        rows = "\n".join(f"{i}. {t.platform} @{t.handle} · {t.role} · {t.why}" for i, t in enumerate(p.targets, start=1))
        return f"Proposed competitive set for *{p.name}* ({p.industry}, {p.geography or 'geography n/a'}):\n{rows}"

    @staticmethod
    def _proposal_blocks(p: Proposal) -> list[dict[str, Any]]:
        rows = "\n".join(
            f"*{i}.* `{t.platform}` @{t.handle} — _{t.role}_ — {t.why}" for i, t in enumerate(p.targets, start=1)
        )
        return [
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Proposed competitive set — {p.name}*\n{p.industry} · {p.geography or 'geography n/a'}"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": rows[:2900]}},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": "Confirm to create the tenant and collect posts (about one credit per account). Or reply in this thread with changes, e.g. `remove 3 and 5` or `add tiktok @brand`."}]},
            {"type": "actions", "elements": [
                {"type": "button", "action_id": "smia_confirm", "style": "primary", "text": {"type": "plain_text", "text": "Confirm set"}},
                {"type": "button", "action_id": "smia_decline", "style": "danger", "text": {"type": "plain_text", "text": "Decline"}},
            ]},
        ]

    # ------------------------------------------------------------------ handlers

    def _register(self) -> None:
        app = self.app

        @app.event("message")
        def on_message(event: dict[str, Any], say: Any) -> None:
            if event.get("bot_id") or event.get("subtype"):
                return
            channel_type = event.get("channel_type")
            text = (event.get("text") or "").strip()
            if channel_type != "im" and not self._mentions_bot(text):
                return
            self._handle_turn(event, text)

        @app.event("app_mention")
        def on_mention(event: dict[str, Any]) -> None:
            if event.get("channel_type") == "im":
                return  # already handled by the message event
            self._handle_turn(event, (event.get("text") or "").strip())

        @app.event("assistant_thread_started")
        def on_assistant_started(event: dict[str, Any]) -> None:
            th = event.get("assistant_thread", {})
            ch, ts = th.get("channel_id"), th.get("thread_ts")
            if ch and ts:
                self._post(ch, ts, "Describe the venture idea: what it is, for whom, and where. I'll find the competitors, confirm them with you, collect their posts, and hand back a playbook.")

        @app.action("smia_confirm")
        def on_confirm(ack: Any, body: dict[str, Any]) -> None:
            ack()
            self._resolve_pending(body, Decision(confirmed=True))

        @app.action("smia_decline")
        def on_decline(ack: Any, body: dict[str, Any]) -> None:
            ack()
            self._resolve_pending(body, Decision(confirmed=False, note="user declined via button; ask what to change"))

    def _mentions_bot(self, text: str) -> bool:
        if self.bot_user_id is None:
            try:
                self.bot_user_id = self.app.client.auth_test()["user_id"]
            except Exception:  # noqa: BLE001
                return False
        return f"<@{self.bot_user_id}>" in text

    def _strip_mention(self, text: str) -> str:
        return re.sub(r"<@[A-Z0-9]+>", "", text).strip()

    def _resolve_pending(self, body: dict[str, Any], decision: Decision) -> None:
        container = body.get("container", {})
        channel = container.get("channel_id") or body.get("channel", {}).get("id")
        thread_ts = container.get("thread_ts") or body.get("message", {}).get("thread_ts") or container.get("message_ts")
        key = f"slack:{channel}:{thread_ts}"
        st = self.threads.get(key)
        if st is None or st.pending is None:
            self._post(channel, thread_ts, "That proposal is no longer waiting for an answer.")
            return
        if decision.confirmed:
            decision.targets = list(st.pending.proposal.targets)
        st.pending.decision = decision
        st.pending.event.set()
        who = body.get("user", {}).get("username") or body.get("user", {}).get("id", "someone")
        self._post(channel, thread_ts, f"{'Confirmed' if decision.confirmed else 'Declined'} by {who}. " + ("Collecting posts now…" if decision.confirmed else ""))

    def _handle_turn(self, event: dict[str, Any], text: str) -> None:
        channel = event["channel"]
        thread_ts = event.get("thread_ts") or event["ts"]
        user = event.get("user", "slack")
        text = self._strip_mention(text)
        if not text:
            return
        st = self._state_for(channel, thread_ts, user)

        # a text reply while a proposal is pending = decline with that note (edits, removals, additions)
        if st.pending is not None:
            st.pending.decision = Decision(confirmed=False, note=text)
            st.pending.event.set()
            self._post(channel, thread_ts, "Got it, revising the set…")
            return

        if not st.lock.acquire(blocking=False):
            self._post(channel, thread_ts, "Still working on the previous message; one moment.")
            return
        try:
            self.app.client.reactions_add(channel=channel, timestamp=event["ts"], name="eyes")
        except Exception:  # noqa: BLE001
            log.debug("reaction failed (missing reactions:write scope?)")
        try:
            res = st.runner.turn(text)
            reply = _mrkdwn(res.reply) if res.reply else "(no reply)"
            footer = f"\n\n_stage: {res.stage} · tools: {', '.join(res.tool_names) or 'none'} · {res.status}_"
            self._post(channel, thread_ts, reply + footer)
        except Exception as e:
            log.exception("turn failed")
            self._post(channel, thread_ts, f"Something went wrong on my side: {e}")
        finally:
            st.lock.release()

    # ------------------------------------------------------------------ run

    def start(self) -> None:
        me = self.app.client.auth_test()
        self.bot_user_id = me["user_id"]
        log.info("SMIA connected to Slack as %s (%s)", me.get("user"), me.get("team"))
        print(f"SMIA is listening in Slack as @{me.get('user')} in {me.get('team')}. DM the app or mention it. Ctrl+C to stop.")
        SocketModeHandler(self.app, self.app_token).start()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    s = get_settings()
    if not s.slack_bot_token or not s.slack_app_token:
        print("SLACK_BOT_TOKEN and SLACK_APP_TOKEN must be set in .env (see .env.example)")
        return 1
    SlackSurface(s.slack_bot_token, s.slack_app_token).start()
    return 0

