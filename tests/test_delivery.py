"""Slack formatting (no network) and review capture (database)."""

from __future__ import annotations

import uuid

import httpx
import pytest

from smia.delivery.slack import _mrkdwn, post_text


def test_mrkdwn_headings_and_bold():
    md = "## 1. Header [D]\nShort video **leads** with 0.89 [T3]."
    out = _mrkdwn(md)
    assert out.splitlines()[0] == "*1. Header [D]*"
    assert "*leads*" in out and "**" not in out


def test_post_text_chunks_and_uses_webhook(monkeypatch):
    calls: list[str] = []

    def handler(req: httpx.Request):
        calls.append(req.read().decode())
        return httpx.Response(200, text="ok")

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    class PatchedClient(real_client):
        def __init__(self, *a, **kw):
            kw["transport"] = transport
            super().__init__(*a, **kw)

    monkeypatch.setattr(httpx, "Client", PatchedClient)
    assert post_text("x" * 8000, webhook="https://hooks.slack.com/services/T/B/x") is True
    assert len(calls) == 3  # 3500 + 3500 + 1000


def test_post_text_without_webhook_is_noop(monkeypatch):
    from smia import settings as st

    monkeypatch.setattr(st, "get_settings", lambda: type("S", (), {"slack_webhook_url": None})())
    import smia.delivery.slack as sl

    monkeypatch.setattr(sl, "get_settings", st.get_settings)
    assert post_text("hello") is False


def _has_db() -> bool:
    try:
        from smia.settings import get_settings
        return bool(get_settings().database_url)
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(not _has_db(), reason="no DATABASE_URL")
def test_record_review_updates_status_and_feeds_notes():
    from sqlalchemy import delete, select

    from smia.db.models import AgentRun, Report, ReviewFeedback, Tenant
    from smia.db.session import db_session
    from smia.delivery.review import activate_tenant, record_review

    name = f"pytest-{uuid.uuid4().hex[:6]}"
    with db_session() as s:
        t = Tenant(name=name, industry="t"); s.add(t); s.flush()
        run = AgentRun(tenant_id=t.id, kind="playbook", model_id="m", prompt_version="p", thresholds_version="1")
        s.add(run); s.flush()
        rep = Report(run_id=run.id, tenant_id=t.id, kind="playbook", period="p", body="b"); s.add(rep); s.flush()
        tid, rid, runid = t.id, rep.id, run.id
    try:
        r = record_review(rid, "revise", "lead with cadence", "tester")
        assert r.status == "revised"
        r = record_review(rid, "approve", None, "tester")
        assert r.status == "approved" and r.delivered_at is not None
        with db_session() as s:
            notes = s.execute(select(ReviewFeedback.notes).where(ReviewFeedback.tenant_id == tid)).scalars().all()
        assert "lead with cadence" in notes
        assert activate_tenant(name).status == "active"
    finally:
        with db_session() as s:
            s.execute(delete(ReviewFeedback).where(ReviewFeedback.tenant_id == tid))
            s.execute(delete(Report).where(Report.id == rid))
            s.execute(delete(AgentRun).where(AgentRun.id == runid))
            s.execute(delete(Tenant).where(Tenant.id == tid))
