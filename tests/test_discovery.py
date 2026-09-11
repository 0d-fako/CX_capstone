"""Discovery tools: verification before proposal, the user gate, the credit cap, and the
validator-gated submit. Fake collector and fake approval; real database."""

from __future__ import annotations

import json
import uuid

import pytest

from smia.agent import session as sess
from smia.agent.trace import RunContext
from smia.collectors.base import CollectResult, HandleInfo, Metrics, RawCapture, TargetSpec
from smia.tools.discovery import Decision, build_discovery_tools


def _has_db() -> bool:
    try:
        from smia.settings import get_settings
        return bool(get_settings().database_url)
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _has_db(), reason="no DATABASE_URL")


class FakeCollector:
    def __init__(self):
        self.resolved = 0

    def resolve_handle(self, platform, handle):
        self.resolved += 1
        if handle.startswith("ghost"):
            return None
        return HandleInfo(platform=platform, handle=handle, display_name=handle.title(), follower_count=1000, post_count=50)

    def collect(self, target: TargetSpec) -> CollectResult:
        from datetime import UTC, datetime
        caps = [RawCapture(platform=target.platform, handle=target.handle, post_id=f"{target.handle}-{i}",
                           posted_at=datetime.now(UTC), content=f"post {i}", media_type="image",
                           metrics=Metrics(likes=10 * i), raw_json={}) for i in range(3)]
        return CollectResult(captures=caps, credits_used=1)


@pytest.fixture
def cleanup():
    created: list[uuid.UUID] = []
    yield created
    from sqlalchemy import delete, select

    from smia.db.models import (
        AgentRun,
        ChatSession,
        MetricSnapshot,
        PipelineRun,
        Post,
        PostLabel,
        Report,
        Target,
        Tenant,
    )
    from smia.db.models import (
        RawCapture as Raw,
    )
    from smia.db.session import db_session
    with db_session() as s:
        for tid in created:
            pids = select(Post.id).where(Post.tenant_id == tid)
            s.execute(delete(PostLabel).where(PostLabel.post_id.in_(pids)))
            s.execute(delete(MetricSnapshot).where(MetricSnapshot.post_id.in_(pids)))
            s.execute(delete(Post).where(Post.tenant_id == tid))
            s.execute(delete(Raw).where(Raw.tenant_id == tid))
            s.execute(delete(PipelineRun).where(PipelineRun.tenant_id == tid))
            s.execute(delete(Report).where(Report.tenant_id == tid))
            s.execute(delete(Target).where(Target.tenant_id == tid))
            s.execute(delete(AgentRun).where(AgentRun.tenant_id == tid))
            s.execute(delete(ChatSession).where(ChatSession.tenant_id == tid))
            s.execute(delete(Tenant).where(Tenant.id == tid))
        s.execute(delete(ChatSession).where(ChatSession.user == "pytest"))


def _tools(approval, collector=None):
    sid = sess.create("pytest")
    ctx = RunContext(tenant_id=None, run_id=uuid.uuid4(), kind="research")
    tools = {t.name: t for t in build_discovery_tools(ctx, sid, approval=approval, collector=collector or FakeCollector())}
    return sid, ctx, tools


def _call(tool, **kw):
    return json.loads(tool.call(kw))


def test_create_prospect_requires_verified_handles_and_user_confirmation(cleanup):
    decisions = [Decision(confirmed=False, note="drop tiktok"), Decision(confirmed=True)]
    sid, ctx, t = _tools(lambda p: decisions.pop(0))
    props = [{"platform": "instagram", "handle": "acme", "role": "direct", "why": "same product"}]
    r = _call(t["create_prospect"], name=f"pytest-{uuid.uuid4().hex[:6]}", industry="coffee", venture_brief="b", targets=props)
    assert r["created"] is False and "verify" in r["error"]
    assert _call(t["resolve_handle"], platform="instagram", handle="@acme")["found"] is True
    assert _call(t["resolve_handle"], platform="instagram", handle="ghost_x")["found"] is False
    name = f"pytest-{uuid.uuid4().hex[:6]}"
    r = _call(t["create_prospect"], name=name, industry="coffee", venture_brief="b", targets=props)
    assert r["created"] is False and "did not confirm" in r["error"] and r["user_note"] == "drop tiktok"
    assert sess.load(sid)["stage"] == "discovering"
    r = _call(t["create_prospect"], name=name, industry="coffee", venture_brief="b", targets=props)
    assert r["created"] is True and ctx.tenant_id is not None
    cleanup.append(ctx.tenant_id)
    st = sess.load(sid)
    assert st["stage"] == "collecting" and st["tenant_id"] == ctx.tenant_id


def test_collect_now_then_submit_report_is_validator_gated(cleanup):
    sid, ctx, t = _tools(lambda p: Decision(confirmed=True))
    _call(t["resolve_handle"], platform="instagram", handle="acme")
    r = _call(t["create_prospect"], name=f"pytest-{uuid.uuid4().hex[:6]}", industry="coffee", venture_brief="b",
              targets=[{"platform": "instagram", "handle": "acme", "role": "direct", "why": "w"}])
    cleanup.append(ctx.tenant_id)
    # the report FK needs a real run row
    from smia.db.models import AgentRun
    from smia.db.session import db_session
    with db_session() as s:
        run = AgentRun(id=ctx.run_id, tenant_id=ctx.tenant_id, kind="research", model_id="x", prompt_version="t", thresholds_version="1")
        s.add(run)
    r = _call(t["collect_now"])
    assert r["collected"] is True and r["posts"] == 3 and r["credits_used"] == 1
    assert sess.load(sid)["stage"] == "ready" and sess.credits_used(sid) == 2  # 1 resolve + 1 collect
    bad = _call(t["submit_report"], kind="playbook", markdown="## 1. Summary [I]\nRE is 9.99 with n=77.", period="p")
    assert bad["accepted"] is False and bad["findings"]
    good = _call(t["submit_report"], kind="playbook", markdown="## 1. Summary [I]\nThree posts were collected [T3].", period="p")
    assert good["accepted"] is True and good["report_id"]


def test_credit_cap_refuses(cleanup, monkeypatch):
    sid, _ctx, t = _tools(lambda p: Decision(confirmed=True))
    sess.add_credits(sid, 60)
    r = _call(t["resolve_handle"], platform="tiktok", handle="acme")
    assert r.get("refused") is True and "cap" in r["error"]
