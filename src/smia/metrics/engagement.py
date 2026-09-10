"""Relative engagement (doc 05 §3). Pure functions over plain records; no database here.

RE(post) = engagement(post) / median engagement of the same account's posts in the trailing
90 days. RE = 1.0 is "normal for this account". Read at the latest snapshot; flagged
"initial" until the post has `maturity_days` of snapshots.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import median
from typing import Any, Literal

Maturity = Literal["initial", "mature"]


@dataclass
class PostObs:
    """One post as the metrics layer sees it: latest totals plus labels."""

    post_id: int
    account_id: uuid.UUID  # targets.id; the account whose median normalises this post
    platform: str
    handle: str
    posted_at: datetime
    likes: int = 0
    comments: int = 0
    shares: int = 0
    views: int | None = None
    snapshot_days: int = 1
    labels: dict[str, str] = field(default_factory=dict)  # dimension -> label
    content: str | None = None
    url: str | None = None

    @property
    def engagement(self) -> int:
        return self.likes + self.comments + self.shares


@dataclass
class RE:
    value: float
    account_median: float
    maturity: Maturity
    n_in_window: int  # posts behind the median


def recency_weight(age_days: float, half_life_days: float) -> float:
    """w = 0.5 ** (age / half_life). Last month matters more; nothing is fully forgotten."""
    if age_days <= 0:
        return 1.0
    return 0.5 ** (age_days / half_life_days)


def weighted_median(values: list[float], weights: list[float]) -> float:
    """Value at which cumulative weight crosses half the total. Empty input raises."""
    if not values:
        raise ValueError("weighted_median of empty input")
    pairs = sorted(zip(values, weights, strict=True))
    total = sum(w for _, w in pairs)
    if total <= 0:
        return median(values)
    acc = 0.0
    for v, w in pairs:
        acc += w
        if acc >= total / 2:
            return v
    return pairs[-1][0]


def account_median(
    posts: list[PostObs], now: datetime, window_days: int, floor: float
) -> tuple[float, int]:
    """Median engagement of an account's posts in the trailing window, floored."""
    cutoff = now - timedelta(days=window_days)
    vals = [float(p.engagement) for p in posts if p.posted_at >= cutoff]
    if not vals:
        return float(floor), 0
    return max(float(floor), median(vals)), len(vals)


def compute_re(posts: list[PostObs], now: datetime, cfg: dict[str, Any]) -> dict[int, RE]:
    """RE for every post, normalised by its own account. cfg = thresholds['engagement']."""
    window = int(cfg["trailing_window_days"])
    floor = float(cfg["median_floor"])
    maturity_days = int(cfg["maturity_days"])

    by_account: dict[uuid.UUID, list[PostObs]] = {}
    for p in posts:
        by_account.setdefault(p.account_id, []).append(p)

    out: dict[int, RE] = {}
    for acct_posts in by_account.values():
        med, n = account_median(acct_posts, now, window, floor)
        for p in acct_posts:
            value = p.engagement / med if med > 0 else 0.0
            if math.isnan(value):
                value = 0.0
            out[p.post_id] = RE(
                value=round(value, 3),
                account_median=med,
                maturity="mature" if p.snapshot_days >= maturity_days else "initial",
                n_in_window=n,
            )
    return out


def age_days(post: PostObs, now: datetime) -> float:
    return max(0.0, (now - post.posted_at).total_seconds() / 86400.0)
