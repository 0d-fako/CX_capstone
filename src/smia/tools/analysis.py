"""Analysis tools the agent calls (doc 09 §5). Thin wrappers over metrics/, labeling/ and
db/. The tenant is bound in the RunContext; the model never supplies it. Every result goes
through ctx.record(), which assigns the ref the model cites.

Post content is returned only under keys named `content_untrusted`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from anthropic import beta_tool
from sqlalchemy import func, select

from smia.agent.trace import RunContext
from smia.db.models import MetricSnapshot, Post, PostLabel, Report, ReviewFeedback, Target
from smia.db.session import db_session
from smia.labeling.label import label_posts as _label_posts
from smia.metrics.benchmarks import benchmarks as _benchmarks
from smia.metrics.benchmarks import cadence_eligible
from smia.metrics.cells import cell_stats as _cell_stats
from smia.metrics.engagement import compute_re
from smia.metrics.loader import load_posts
from smia.settings import load_thresholds

EXCERPT = 240


def _excerpt(text: str | None) -> str:
    return (text or "").replace("\n", " ").strip()[:EXCERPT]


class NoTenantYet(Exception):
    """Raised by analysis tools before a research session has a confirmed tenant."""


def build_analysis_tools(ctx: RunContext) -> list[Any]:
    thresholds = load_thresholds()

    def _tid() -> uuid.UUID:
        if ctx.tenant_id is None:
            raise NoTenantYet("no tenant yet: discover competitors, confirm the set with the user, then collect")
        return ctx.tenant_id

    def _now() -> datetime:
        return datetime.now(UTC)

    def _loaded(days: int, platform: str | None = None, handles: list[str] | None = None, content: bool = False):
        with db_session() as s:
            posts = load_posts(s, _tid(), since=_now() - timedelta(days=days), platform=platform,
                               handles=handles, include_content=content)
        return posts, compute_re(posts, _now(), thresholds["engagement"])

    @beta_tool
    def benchmarks(window_days: int = 90) -> str:
        """Per-account benchmarks: posts per week, format mix, median relative engagement, trend arrow, n.

        Also reports whether a cadence recommendation is allowed under the evidence thresholds.
        Accounts whose sample is "most popular posts" rather than latest carry a caveat and no cadence.

        Args:
            window_days: trailing window in days (default 90).
        """
        posts, re = _loaded(window_days)
        bms = _benchmarks(posts, re, now=_now(), thresholds=thresholds, window_days=window_days)
        ok, why = cadence_eligible(bms, thresholds)
        out = {
            "window_days": window_days,
            "posts_considered": len(posts),
            "accounts": [b.as_dict() for b in bms],
            "cadence_recommendation_allowed": ok,
            "cadence_note": why,
        }
        return ctx.record("benchmarks", {"window_days": window_days}, out)

    @beta_tool
    def cell_stats(dimension: str, cross_dimension: str | None = None, window_days: int = 90) -> str:
        """Score every cell on a labelling dimension: n, recency-weighted median RE, trend, share, confidence.

        Cells below the evidence threshold come back as `insufficient` with their n and no numbers;
        they may only be reported under "Collecting". Use `list_dimensions` to see what exists, or
        `label_posts` to create a new dimension first.

        Args:
            dimension: dimension name, e.g. "format" or one you created with label_posts.
            cross_dimension: optional second dimension to cross with, e.g. format x tone.
            window_days: trailing window in days (default 90).
        """
        posts, re = _loaded(window_days)
        cells = _cell_stats(posts, re, dimension, cross_dimension=cross_dimension, now=_now(),
                            thresholds=thresholds, window_days=window_days)
        out = {
            "dimension": dimension, "cross_dimension": cross_dimension, "window_days": window_days,
            "posts_with_dimension": sum(c.n for c in cells),
            "cells": [c.as_dict() for c in cells],
            "thresholds": {
                "cell_min_n": thresholds["eligibility"]["cell_min_n"],
                "directional_min_n": thresholds["confidence"]["directional_min_n"],
                "established_min_n": thresholds["confidence"]["established_min_n"],
            },
        }
        return ctx.record("cell_stats", {"dimension": dimension, "cross_dimension": cross_dimension,
                                         "window_days": window_days}, out)

    @beta_tool
    def query_posts(
        platform: str | None = None,
        handles: list[str] | None = None,
        dimension: str | None = None,
        label: str | None = None,
        min_re: float | None = None,
        order: str = "re",
        limit: int = 15,
        window_days: int = 90,
    ) -> str:
        """List posts with their relative engagement, format and a content excerpt.

        Args:
            platform: instagram, tiktok or twitter; omit for all.
            handles: restrict to these account handles.
            dimension: with `label`, keep only posts carrying that label on this dimension.
            label: the label to filter on (requires dimension).
            min_re: keep only posts with relative engagement at or above this value.
            order: "re" for highest relative engagement first, "recent" for newest first.
            limit: max rows, 1 to 50.
            window_days: trailing window in days (default 90).
        """
        limit = max(1, min(50, limit))
        posts, re = _loaded(window_days, platform=platform, handles=handles, content=True)
        rows = [p for p in posts if p.post_id in re]
        if dimension and label:
            rows = [p for p in rows if p.labels.get(dimension) == label]
        if min_re is not None:
            rows = [p for p in rows if re[p.post_id].value >= min_re]
        rows.sort(key=(lambda p: re[p.post_id].value) if order == "re" else (lambda p: p.posted_at), reverse=True)
        out = {
            "matched": len(rows),
            "returned": min(limit, len(rows)),
            "posts": [
                {
                    "post_id": p.post_id, "platform": p.platform, "handle": p.handle,
                    "posted_at": p.posted_at.date().isoformat(),
                    "format": p.labels.get("format"), "labels": p.labels,
                    "re": re[p.post_id].value, "maturity": re[p.post_id].maturity,
                    "likes": p.likes, "comments": p.comments, "shares": p.shares, "views": p.views,
                    "url": p.url, "content_untrusted": _excerpt(p.content),
                }
                for p in rows[:limit]
            ],
        }
        return ctx.record("query_posts", {"platform": platform, "handles": handles, "dimension": dimension,
                                          "label": label, "min_re": min_re, "order": order,
                                          "limit": limit, "window_days": window_days}, out)

    @beta_tool
    def get_post(post_id: int) -> str:
        """Full content, labels, relative engagement and the daily engagement curve for one post.

        Args:
            post_id: the post id from query_posts, cell_stats exemplars or benchmarks.
        """
        with db_session() as s:
            row = s.execute(
                select(Post, Target.handle).join(Target, Target.id == Post.target_id)
                .where(Post.id == post_id, Post.tenant_id == _tid())
            ).first()
            if row is None:
                return ctx.record("get_post", {"post_id": post_id}, {"found": False}, error="not found for this tenant")
            post, handle = row
            snaps = s.execute(
                select(MetricSnapshot).where(MetricSnapshot.post_id == post_id).order_by(MetricSnapshot.captured_on)
            ).scalars().all()
            labels = dict(s.execute(select(PostLabel.dimension, PostLabel.label).where(PostLabel.post_id == post_id)).all())
        _, re = _loaded(365, platform=post.platform, handles=[handle])
        r = re.get(post_id)
        out = {
            "found": True, "post_id": post_id, "platform": post.platform, "handle": handle,
            "posted_at": post.posted_at.isoformat(), "url": post.url, "media_type": post.media_type,
            "media_duration_s": post.media_duration_s, "labels": labels,
            "re": r.value if r else None, "maturity": r.maturity if r else None,
            "account_median_engagement": r.account_median if r else None,
            "curve": [{"day": x.captured_on.isoformat(), "likes": x.likes, "comments": x.comments,
                       "shares": x.shares, "views": x.views} for x in snaps],
            "content_untrusted": (post.content or "")[:2000],
        }
        return ctx.record("get_post", {"post_id": post_id}, out)

    @beta_tool
    def list_dimensions() -> str:
        """Which labelling dimensions exist for this tenant and how many posts carry each label."""
        with db_session() as s:
            rows = s.execute(
                select(PostLabel.dimension, PostLabel.label, PostLabel.source, func.count(PostLabel.id))
                .join(Post, Post.id == PostLabel.post_id).where(Post.tenant_id == _tid())
                .group_by(PostLabel.dimension, PostLabel.label, PostLabel.source)
            ).all()
        dims: dict[str, dict[str, Any]] = {}
        for dim, lab, src, n in rows:
            d = dims.setdefault(dim, {"source": src, "labels": {}})
            d["labels"][lab] = d["labels"].get(lab, 0) + n
        return ctx.record("list_dimensions", {}, {"dimensions": dims})

    @beta_tool
    def label_posts(dimension: str, definition: str, labels: list[str], post_ids: list[int] | None = None) -> str:
        """Label a sample of posts on a dimension you define, so cell_stats can test a hypothesis.

        A cheap model reads each post and assigns exactly one of your labels (plus "other").
        Results are cached per definition. After this, call cell_stats(dimension) to get the numbers.

        Args:
            dimension: short name, e.g. "tone" or "has_person_on_camera".
            definition: one or two sentences saying how to decide the label from the post text.
            labels: two to eight mutually exclusive labels; "other" is added automatically.
            post_ids: which posts to label; omit to label up to 100 most recent posts with text.
        """
        with db_session() as s:
            if not post_ids:
                post_ids = list(s.execute(
                    select(Post.id).where(Post.tenant_id == _tid(), Post.content.isnot(None))
                    .order_by(Post.posted_at.desc()).limit(100)
                ).scalars())
            res = _label_posts(s, _tid(), dimension, definition, labels, post_ids, run_id=ctx.run_id)
        return ctx.record("label_posts", {"dimension": dimension, "definition": definition, "labels": labels,
                                          "post_ids": post_ids[:5] + (["..."] if len(post_ids) > 5 else [])}, res.as_dict())

    @beta_tool
    def reviewer_notes() -> str:
        """The last five notes human reviewers left on this tenant's reports, most recent first."""
        with db_session() as s:
            rows = s.execute(
                select(ReviewFeedback.action, ReviewFeedback.notes, ReviewFeedback.created_at)
                .where(ReviewFeedback.tenant_id == _tid(), ReviewFeedback.notes.isnot(None))
                .order_by(ReviewFeedback.created_at.desc()).limit(5)
            ).all()
        out = {"notes": [{"action": a, "note": n, "at": t.isoformat()} for a, n, t in rows]}
        return ctx.record("reviewer_notes", {}, out)

    @beta_tool
    def prior_report(kind: str = "digest") -> str:
        """The most recent approved report of this kind for continuity ("since last week").

        Args:
            kind: "digest" or "playbook".
        """
        with db_session() as s:
            row = s.execute(
                select(Report.period, Report.body, Report.created_at)
                .where(Report.tenant_id == _tid(), Report.kind == kind, Report.status.in_(["approved", "delivered"]))
                .order_by(Report.created_at.desc()).limit(1)
            ).first()
        out = {"found": row is not None}
        if row:
            out.update({"period": row[0], "created_at": row[2].isoformat(), "body": row[1][:6000]})
        return ctx.record("prior_report", {"kind": kind}, out)

    return [benchmarks, cell_stats, query_posts, get_post, list_dimensions, label_posts, reviewer_notes, prior_report]


def tenant_summary(tenant_id: uuid.UUID) -> dict[str, Any]:
    """Facts for the brief header: targets, post counts, collection span."""
    with db_session() as s:
        targets = s.execute(select(Target).where(Target.tenant_id == tenant_id, Target.active.is_(True))).scalars().all()
        n_posts, first, last = s.execute(
            select(func.count(Post.id), func.min(Post.posted_at), func.max(Post.posted_at)).where(Post.tenant_id == tenant_id)
        ).one()
        snap_days = s.execute(
            select(func.count(func.distinct(MetricSnapshot.captured_on))).join(Post, Post.id == MetricSnapshot.post_id)
            .where(Post.tenant_id == tenant_id)
        ).scalar_one()
    return {
        "targets": [{"platform": t.platform, "handle": t.handle, "role": t.role, "ownership": t.ownership} for t in targets],
        "posts": int(n_posts or 0),
        "earliest_post": first.date().isoformat() if first else None,
        "latest_post": last.date().isoformat() if last else None,
        "snapshot_days": int(snap_days or 0),
    }
