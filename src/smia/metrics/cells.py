"""Cell scores and eligibility (doc 05 §4-5). A cell is one label on a dimension, or a pair
of labels across two dimensions. Per cell: n, recency-weighted median RE, trend, share, and
a confidence label. Below threshold the cell is `insufficient` and the agent may only list
it under "collecting". Thresholds come from config, never from the caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from smia.metrics.engagement import RE, PostObs, age_days, recency_weight, weighted_median

Confidence = Literal["established", "directional", "insufficient"]


@dataclass
class CellStat:
    dimension: str
    label: str
    cross_dimension: str | None
    cross_label: str | None
    n: int
    re_med: float | None  # None when insufficient
    trend: float | None  # last window / prior window, None when either window is thin
    share: float  # cell posts / all posts considered
    confidence: Confidence
    exemplar_post_ids: list[int]  # top-RE posts in the cell, for get_post lookups
    maturity_initial_share: float  # fraction of cell posts whose RE is still "initial"

    @property
    def status(self) -> str:
        return "insufficient" if self.confidence == "insufficient" else "eligible"

    def as_dict(self) -> dict[str, Any]:
        d = self.__dict__.copy()
        d["status"] = self.status
        return d


def confidence_for(n: int, cfg_conf: dict[str, Any], cfg_elig: dict[str, Any]) -> Confidence:
    if n >= int(cfg_conf["established_min_n"]):
        return "established"
    if n >= int(cfg_conf["directional_min_n"]) and n >= int(cfg_elig["cell_min_n"]):
        return "directional"
    return "insufficient"


def _wmed(posts: list[PostObs], re: dict[int, RE], now: datetime, half_life: float) -> float:
    vals = [re[p.post_id].value for p in posts]
    wts = [recency_weight(age_days(p, now), half_life) for p in posts]
    return round(weighted_median(vals, wts), 3)


def _trend(
    posts: list[PostObs], re: dict[int, RE], now: datetime, cfg_elig: dict[str, Any], half_life: float
) -> float | None:
    w = int(cfg_elig["trend_window_days"])
    min_n = int(cfg_elig["trend_min_n_per_window"])
    last = [p for p in posts if p.posted_at >= now - timedelta(days=w)]
    prior = [
        p for p in posts if now - timedelta(days=2 * w) <= p.posted_at < now - timedelta(days=w)
    ]
    if len(last) < min_n or len(prior) < min_n:
        return None
    a = _wmed(last, re, now, half_life)
    b = _wmed(prior, re, now, half_life)
    if b <= 0:
        return None
    return round(a / b, 3)


def cell_stats(
    posts: list[PostObs],
    re: dict[int, RE],
    dimension: str,
    *,
    now: datetime,
    thresholds: dict[str, Any],
    cross_dimension: str | None = None,
    window_days: int | None = None,
    exemplars: int = 3,
) -> list[CellStat]:
    """Group posts by label (or label pair) and score each cell. Posts lacking the label on
    the dimension are excluded from that dimension's universe, so `share` sums to 1 over
    labelled posts only."""
    cfg_e = thresholds["engagement"]
    cfg_elig = thresholds["eligibility"]
    cfg_conf = thresholds["confidence"]
    half_life = float(cfg_e["recency_half_life_days"])
    window = int(window_days or cfg_e["trailing_window_days"])
    cutoff = now - timedelta(days=window)

    universe = [
        p
        for p in posts
        if p.posted_at >= cutoff
        and dimension in p.labels
        and (cross_dimension is None or cross_dimension in p.labels)
        and p.post_id in re
    ]
    if not universe:
        return []

    groups: dict[tuple[str, str | None], list[PostObs]] = {}
    for p in universe:
        key = (p.labels[dimension], p.labels[cross_dimension] if cross_dimension else None)
        groups.setdefault(key, []).append(p)

    total = len(universe)
    out: list[CellStat] = []
    for (label, xlabel), cell in groups.items():
        n = len(cell)
        conf = confidence_for(n, cfg_conf, cfg_elig)
        eligible = conf != "insufficient"
        ranked = sorted(cell, key=lambda p: re[p.post_id].value, reverse=True)
        out.append(
            CellStat(
                dimension=dimension,
                label=label,
                cross_dimension=cross_dimension,
                cross_label=xlabel,
                n=n,
                re_med=_wmed(cell, re, now, half_life) if eligible else None,
                trend=_trend(cell, re, now, cfg_elig, half_life) if eligible else None,
                share=round(n / total, 3),
                confidence=conf,
                exemplar_post_ids=[p.post_id for p in ranked[:exemplars]] if eligible else [],
                maturity_initial_share=round(
                    sum(1 for p in cell if re[p.post_id].maturity == "initial") / n, 3
                ),
            )
        )
    # eligible first, by RE_med desc then trend; insufficient after, by n desc
    out.sort(
        key=lambda c: (
            c.confidence == "insufficient",
            -(c.re_med or 0.0),
            -(c.trend or 0.0),
            -c.n,
        )
    )
    return out
