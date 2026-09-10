"""Format rule (pure) and, when DATABASE_URL points at a real Postgres, upsert idempotency."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest

from smia.collectors.base import CollectResult, Metrics, RawCapture, TargetSpec
from smia.ingestion.normalize import definition_hash, format_label


@pytest.mark.parametrize(
    "media,dur,expected",
    [
        ("video", 120, "long_video"),
        ("video", 89, "short_video"),
        ("video", 90, "long_video"),
        ("video", None, "short_video"),
        ("image", None, "static_image"),
        ("carousel", None, "carousel"),
        ("text", None, "text_post"),
        ("link", None, "link_share"),
        (None, None, "other"),
    ],
)
def test_format_rule(media, dur, expected):
    assert format_label(media, dur) == expected


def test_definition_hash_is_stable_and_whitespace_insensitive():
    assert definition_hash("a b") == definition_hash("  a b \n")
    assert len(definition_hash("x")) == 16


# --------------------------------------------------------------------------- integration

needs_db = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set; integration test skipped"
)


class FakeCollector:
    def __init__(self, captures):
        self.captures = captures

    def collect(self, target: TargetSpec) -> CollectResult:
        return CollectResult(captures=self.captures, credits_used=1)

    def resolve_handle(self, platform, handle):
        return None


def _cap(pid: str, likes: int) -> RawCapture:
    return RawCapture(
        platform="instagram", handle="acme_test", post_id=pid,
        posted_at=datetime(2026, 9, 1, tzinfo=timezone.utc), content=f"post {pid}",
        media_type="video", media_duration_s=100, url=f"https://example/{pid}",
        metrics=Metrics(likes=likes, comments=1), raw_json={"pk": pid},
    )


@needs_db
def test_collect_twice_is_idempotent():
    from sqlalchemy import func, select

    from smia.db.models import MetricSnapshot, Post, PostLabel, Target, Tenant
    from smia.db.session import db_session
    from smia.pipeline import collect

    name = f"pytest-{uuid.uuid4().hex[:8]}"
    with db_session() as s:
        t = Tenant(name=name, industry="test")
        s.add(t); s.flush()
        s.add(Target(tenant_id=t.id, platform="instagram", handle="acme_test"))
        tid = t.id

    caps = [_cap("p1", 10), _cap("p2", 20)]
    with db_session() as s:
        a = collect(s, tid, FakeCollector(caps))
    with db_session() as s:
        b = collect(s, tid, FakeCollector([_cap("p1", 15), _cap("p2", 20)]))  # p1 edited/grew

    assert a.posts == 2 and b.posts == 2 and a.credits_used == b.credits_used == 1
    with db_session() as s:
        n_posts = s.scalar(select(func.count()).select_from(Post).where(Post.tenant_id == tid))
        pids = [r for r in s.execute(select(Post.id).where(Post.tenant_id == tid)).scalars()]
        n_snaps = s.scalar(select(func.count()).select_from(MetricSnapshot).where(MetricSnapshot.post_id.in_(pids)))
        n_labels = s.scalar(select(func.count()).select_from(PostLabel).where(PostLabel.post_id.in_(pids)))
        likes = dict(s.execute(select(Post.post_id, MetricSnapshot.likes).join(MetricSnapshot).where(Post.tenant_id == tid)).all())
        labels = set(s.execute(select(PostLabel.label).where(PostLabel.post_id.in_(pids))).scalars())
    assert n_posts == 2                      # no duplicates
    assert n_snaps == 2                      # same day upserted, not appended
    assert likes["p1"] == 15                 # today's snapshot reflects the latest numbers
    assert n_labels == 2 and labels == {"long_video"}


@needs_db
def test_canary_on_silent_target():
    from smia.db.models import PipelineRun, Target, Tenant
    from smia.db.session import db_session
    from smia.pipeline import collect

    name = f"pytest-{uuid.uuid4().hex[:8]}"
    with db_session() as s:
        t = Tenant(name=name, industry="test"); s.add(t); s.flush()
        s.add(Target(tenant_id=t.id, platform="tiktok", handle="silent")); tid = t.id
    with db_session() as s:
        summary = collect(s, tid, FakeCollector([]))
        run = s.get(PipelineRun, summary.pipeline_run_id)
        assert run.status == "canary" and "tiktok/silent" in (run.error or "")
    assert summary.canaries and summary.posts == 0
