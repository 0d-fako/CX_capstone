"""Ingestion: RawCapture -> raw_captures + posts + metric_snapshots + rule format label.

Everything here is an upsert keyed on (platform, post_id) or (post_id, captured_on), so a
rerun after a failed night is fixed by running again, never by cleaning up.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from smia.collectors.base import RawCapture
from smia.db.models import MetricSnapshot, Post, PostLabel, Target
from smia.db.models import RawCapture as RawCaptureRow
from smia.settings import load_dimensions

FORMAT_DIMENSION = "format"
LONG_VIDEO_MIN_S = 90


def format_label(media_type: str | None, duration_s: int | None) -> str:
    """The one pre-promoted, rule-based dimension (doc 09 §5)."""
    if media_type == "video":
        if duration_s is None:
            return "short_video"  # most short-form platforms; long-form needs the duration to prove it
        return "long_video" if duration_s >= LONG_VIDEO_MIN_S else "short_video"
    return {
        "image": "static_image",
        "carousel": "carousel",
        "text": "text_post",
        "link": "link_share",
    }.get(media_type or "", "other")


def definition_hash(definition: str) -> str:
    return hashlib.sha256(definition.strip().encode("utf-8")).hexdigest()[:16]


def format_definition_hash() -> str:
    return definition_hash(load_dimensions()[FORMAT_DIMENSION]["definition"])


@dataclass
class IngestStats:
    raw: int = 0
    posts_upserted: int = 0
    snapshots_upserted: int = 0
    labels_written: int = 0
    post_ids: list[int] = field(default_factory=list)


def ingest(
    session: Session,
    tenant_id: uuid.UUID,
    target: Target,
    captures: list[RawCapture],
    captured_on: date | None = None,
) -> IngestStats:
    stats = IngestStats()
    if not captures:
        return stats
    captured_on = captured_on or datetime.now(UTC).date()
    fdh = format_definition_hash()

    # 1. untouched vendor payloads, one row per capture (the replay source)
    session.execute(
        insert(RawCaptureRow),
        [
            {"tenant_id": tenant_id, "target_id": target.id, "payload": c.raw_json}
            for c in captures
        ],
    )
    stats.raw = len(captures)

    # 2. posts: upsert on (platform, post_id); content/media may have been edited
    post_rows = [
        {
            "tenant_id": tenant_id,
            "target_id": target.id,
            "platform": c.platform,
            "post_id": c.post_id,
            "posted_at": c.posted_at,
            "content": c.content,
            "media_type": c.media_type,
            "media_duration_s": c.media_duration_s,
            "url": c.url,
        }
        for c in captures
    ]
    stmt = insert(Post).values(post_rows)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_post_platform_id",
        set_={
            "content": stmt.excluded.content,
            "media_type": stmt.excluded.media_type,
            "media_duration_s": stmt.excluded.media_duration_s,
            "url": stmt.excluded.url,
        },
    ).returning(Post.id, Post.post_id)
    id_by_post_id = {pid: i for i, pid in session.execute(stmt).all()}
    stats.posts_upserted = len(id_by_post_id)
    stats.post_ids = list(id_by_post_id.values())

    # 3. today's snapshot per post: upsert on (post_id, captured_on)
    snap_rows = [
        {
            "post_id": id_by_post_id[c.post_id],
            "captured_on": captured_on,
            "likes": c.metrics.likes,
            "comments": c.metrics.comments,
            "shares": c.metrics.shares,
            "views": c.metrics.views,
        }
        for c in captures
        if c.post_id in id_by_post_id
    ]
    s2 = insert(MetricSnapshot).values(snap_rows)
    s2 = s2.on_conflict_do_update(
        constraint="uq_snapshot_post_day",
        set_={
            "likes": s2.excluded.likes,
            "comments": s2.excluded.comments,
            "shares": s2.excluded.shares,
            "views": s2.excluded.views,
        },
    )
    session.execute(s2)
    stats.snapshots_upserted = len(snap_rows)

    # 4. rule-based format label; never overwrite an existing label for this definition
    label_rows = [
        {
            "post_id": id_by_post_id[c.post_id],
            "dimension": FORMAT_DIMENSION,
            "label": format_label(c.media_type, c.media_duration_s),
            "source": "rule",
            "definition_hash": fdh,
        }
        for c in captures
        if c.post_id in id_by_post_id
    ]
    s3 = insert(PostLabel).values(label_rows).on_conflict_do_nothing(
        constraint="uq_label_post_dim_def"
    )
    res = session.execute(s3)
    stats.labels_written = res.rowcount or 0
    return stats


def existing_post_count(session: Session, tenant_id: uuid.UUID) -> int:
    return len(session.execute(select(Post.id).where(Post.tenant_id == tenant_id)).all())
