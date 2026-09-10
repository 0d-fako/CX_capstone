"""Build PostObs records for a tenant from posts + latest snapshot + labels. The only
place in metrics/ that touches the database; everything else is pure."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from smia.db.models import MetricSnapshot, Post, PostLabel, Target
from smia.metrics.engagement import PostObs


def load_posts(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    since: datetime | None = None,
    platform: str | None = None,
    handles: list[str] | None = None,
    include_content: bool = False,
) -> list[PostObs]:
    latest = (
        select(
            MetricSnapshot.post_id,
            func.max(MetricSnapshot.captured_on).label("last_day"),
            func.count(MetricSnapshot.id).label("days"),
        )
        .group_by(MetricSnapshot.post_id)
        .subquery()
    )
    q = (
        select(
            Post.id,
            Post.target_id,
            Post.platform,
            Target.handle,
            Post.posted_at,
            Post.content if include_content else func.cast(None, Post.content.type),
            Post.url,
            MetricSnapshot.likes,
            MetricSnapshot.comments,
            MetricSnapshot.shares,
            MetricSnapshot.views,
            latest.c.days,
        )
        .join(Target, Target.id == Post.target_id)
        .join(latest, latest.c.post_id == Post.id)
        .join(
            MetricSnapshot,
            (MetricSnapshot.post_id == Post.id) & (MetricSnapshot.captured_on == latest.c.last_day),
        )
        .where(Post.tenant_id == tenant_id)
    )
    if since is not None:
        q = q.where(Post.posted_at >= since)
    if platform:
        q = q.where(Post.platform == platform)
    if handles:
        q = q.where(Target.handle.in_([h.lstrip("@") for h in handles]))

    rows = session.execute(q).all()
    if not rows:
        return []
    ids = [r[0] for r in rows]
    labels: dict[int, dict[str, str]] = {}
    for pid, dim, lab in session.execute(
        select(PostLabel.post_id, PostLabel.dimension, PostLabel.label).where(PostLabel.post_id.in_(ids))
    ).all():
        labels.setdefault(pid, {})[dim] = lab

    return [
        PostObs(
            post_id=r[0],
            account_id=r[1],
            platform=r[2],
            handle=r[3],
            posted_at=r[4],
            content=r[5],
            url=r[6],
            likes=r[7] or 0,
            comments=r[8] or 0,
            shares=r[9] or 0,
            views=r[10],
            snapshot_days=int(r[11] or 1),
            labels=labels.get(r[0], {}),
        )
        for r in rows
    ]


def default_since(now: datetime, window_days: int) -> datetime:
    return now - timedelta(days=window_days)
