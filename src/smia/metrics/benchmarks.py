"""Per-target benchmarks (playbook §2 and §4): cadence, format mix, RE_med, trend arrow."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from smia.metrics.engagement import RE, PostObs, age_days, recency_weight, weighted_median

# Platforms whose sample is not a time series of recent posts (doc: vendor caveats).
CADENCE_UNRELIABLE = {"twitter": "sample is the account's most popular posts, not the latest"}


@dataclass
class TargetBenchmark:
    account_id: uuid.UUID
    platform: str
    handle: str
    n: int
    weeks_observed: float
    posts_per_week: float | None
    format_mix: dict[str, float]  # label -> share, sums to ~1
    re_med: float | None
    trend: float | None  # last 28d RE_med / prior 28d
    trend_arrow: str  # "up" | "down" | "flat" | "n/a"
    account_median_engagement: float
    caveat: str | None = None
    top_post_ids: list[int] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        d = self.__dict__.copy()
        d["account_id"] = str(self.account_id)
        return d


def _arrow(trend: float | None) -> str:
    if trend is None:
        return "n/a"
    if trend >= 1.15:
        return "up"
    if trend <= 0.87:
        return "down"
    return "flat"


def benchmarks(
    posts: list[PostObs],
    re: dict[int, RE],
    *,
    now: datetime,
    thresholds: dict[str, Any],
    window_days: int | None = None,
) -> list[TargetBenchmark]:
    cfg_e = thresholds["engagement"]
    cfg_elig = thresholds["eligibility"]
    half_life = float(cfg_e["recency_half_life_days"])
    window = int(window_days or cfg_e["trailing_window_days"])
    cutoff = now - timedelta(days=window)
    tw = int(cfg_elig["trend_window_days"])
    min_n = int(cfg_elig["trend_min_n_per_window"])

    by_acct: dict[uuid.UUID, list[PostObs]] = {}
    for p in posts:
        if p.posted_at >= cutoff and p.post_id in re:
            by_acct.setdefault(p.account_id, []).append(p)

    out: list[TargetBenchmark] = []
    for acct, ps in by_acct.items():
        ps.sort(key=lambda p: p.posted_at)
        first = ps[0].posted_at
        # weeks observed = span of the sample, at least one week, capped at the window
        span_days = max(7.0, min(float(window), (now - first).total_seconds() / 86400.0))
        weeks = round(span_days / 7.0, 2)
        caveat = CADENCE_UNRELIABLE.get(ps[0].platform)
        ppw = None if caveat else round(len(ps) / weeks, 2)

        fmt: dict[str, int] = {}
        for p in ps:
            f = p.labels.get("format")
            if f:
                fmt[f] = fmt.get(f, 0) + 1
        fmt_total = sum(fmt.values())
        mix = {k: round(v / fmt_total, 3) for k, v in sorted(fmt.items(), key=lambda kv: -kv[1])} if fmt_total else {}

        vals = [re[p.post_id].value for p in ps]
        wts = [recency_weight(age_days(p, now), half_life) for p in ps]
        re_med = round(weighted_median(vals, wts), 3)

        last_w = [p for p in ps if p.posted_at >= now - timedelta(days=tw)]
        prior_w = [p for p in ps if now - timedelta(days=2 * tw) <= p.posted_at < now - timedelta(days=tw)]
        trend = None
        if len(last_w) >= min_n and len(prior_w) >= min_n:
            a = weighted_median([re[p.post_id].value for p in last_w], [recency_weight(age_days(p, now), half_life) for p in last_w])
            b = weighted_median([re[p.post_id].value for p in prior_w], [recency_weight(age_days(p, now), half_life) for p in prior_w])
            trend = round(a / b, 3) if b > 0 else None

        top = sorted(ps, key=lambda p: re[p.post_id].value, reverse=True)[:3]
        out.append(
            TargetBenchmark(
                account_id=acct,
                platform=ps[0].platform,
                handle=ps[0].handle,
                n=len(ps),
                weeks_observed=weeks,
                posts_per_week=ppw,
                format_mix=mix,
                re_med=re_med,
                trend=trend,
                trend_arrow=_arrow(trend),
                account_median_engagement=re[ps[0].post_id].account_median,
                caveat=caveat,
                top_post_ids=[p.post_id for p in top],
            )
        )
    out.sort(key=lambda b: (b.platform, -(b.re_med or 0)))
    return out


def cadence_eligible(benchmarks_: list[TargetBenchmark], thresholds: dict[str, Any]) -> tuple[bool, str]:
    """Doc 05 §5: a cadence recommendation needs >= 4 weeks history and >= 3 active competitors."""
    cfg = thresholds["eligibility"]
    usable = [b for b in benchmarks_ if b.posts_per_week is not None]
    if len(usable) < int(cfg["cadence_min_competitors"]):
        return False, f"only {len(usable)} accounts with reliable cadence; need {cfg['cadence_min_competitors']}"
    if min(b.weeks_observed for b in usable) < int(cfg["cadence_min_weeks"]):
        return False, f"fewer than {cfg['cadence_min_weeks']} weeks observed for at least one account"
    return True, "ok"
