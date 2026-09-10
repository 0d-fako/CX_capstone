"""Synthetic-data proofs for the evidence ladder (doc 05 §3-5). No database."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from smia.metrics.benchmarks import benchmarks, cadence_eligible
from smia.metrics.cells import cell_stats
from smia.metrics.engagement import PostObs, compute_re, recency_weight, weighted_median
from smia.settings import load_thresholds

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
T = load_thresholds()
ACCT = uuid.uuid4()


def post(pid: int, eng: int, days_ago: float, acct=ACCT, days=7, platform="instagram", **labels) -> PostObs:
    return PostObs(
        post_id=pid, account_id=acct, platform=platform, handle="acme",
        posted_at=NOW - timedelta(days=days_ago), likes=eng, snapshot_days=days, labels=labels,
    )


# --------------------------------------------------------------------------- RE


def test_median_post_has_re_of_one():
    posts = [post(i, e, days_ago=i) for i, e in enumerate([10, 20, 30, 40, 50], start=1)]
    re = compute_re(posts, NOW, T["engagement"])
    assert re[3].value == 1.0            # 30 is the median
    assert re[5].value == pytest.approx(50 / 30, abs=1e-3)
    assert re[3].n_in_window == 5


def test_re_is_per_account():
    big = uuid.uuid4()
    posts = [post(1, 100, 1), post(2, 300, 2), post(3, 1000, 1, acct=big), post(4, 3000, 2, acct=big)]
    re = compute_re(posts, NOW, T["engagement"])
    # same relative pull on two accounts of very different size
    assert re[2].value == re[4].value


def test_maturity_flag():
    posts = [post(1, 10, 1, days=2), post(2, 10, 1, days=7)]
    re = compute_re(posts, NOW, T["engagement"])
    assert re[1].maturity == "initial" and re[2].maturity == "mature"


def test_posts_outside_window_do_not_feed_the_median():
    posts = [post(1, 10, 1), post(2, 10, 2), post(3, 10_000, 200)]  # 200 days old
    re = compute_re(posts, NOW, T["engagement"])
    assert re[1].account_median == 10 and re[1].n_in_window == 2


def test_median_floor_guards_dead_accounts():
    posts = [post(1, 0, 1), post(2, 0, 2)]
    re = compute_re(posts, NOW, T["engagement"])
    assert re[1].account_median == 1 and re[1].value == 0.0


# --------------------------------------------------------------------------- weighting


def test_recency_weight_half_life():
    assert recency_weight(0, 28) == 1.0
    assert recency_weight(28, 28) == pytest.approx(0.5)
    assert recency_weight(56, 28) == pytest.approx(0.25)


def test_recency_weighting_shifts_the_median():
    # old posts strong, recent posts weak: unweighted median is high, weighted median is low
    vals = [3.0, 3.0, 3.0, 1.0, 1.0, 1.0]
    old = [recency_weight(80, 28)] * 3
    new = [recency_weight(1, 28)] * 3
    assert weighted_median(vals, [1] * 6) == 3.0 or weighted_median(vals, [1] * 6) == 1.0  # tie region
    assert weighted_median(vals, old + new) == 1.0


# --------------------------------------------------------------------------- cells & eligibility


def _cell_posts(n: int, label: str, eng: int, start_pid: int = 1, spread_days: int = 60):
    return [
        post(start_pid + i, eng + i, days_ago=(i * spread_days / max(1, n)) + 1, format=label)
        for i in range(n)
    ]


def test_four_post_cell_is_insufficient_and_has_no_numbers():
    posts = _cell_posts(4, "carousel", 50) + _cell_posts(20, "short_video", 10, start_pid=100)
    re = compute_re(posts, NOW, T["engagement"])
    cells = {c.label: c for c in cell_stats(posts, re, "format", now=NOW, thresholds=T)}
    c = cells["carousel"]
    assert c.confidence == "insufficient" and c.status == "insufficient"
    assert c.re_med is None and c.trend is None and c.exemplar_post_ids == []
    assert c.n == 4 and c.share == pytest.approx(4 / 24, abs=1e-3)


def test_twelve_post_cell_is_directional_thirty_is_established():
    posts = _cell_posts(12, "carousel", 50) + _cell_posts(30, "short_video", 10, start_pid=100)
    re = compute_re(posts, NOW, T["engagement"])
    cells = {c.label: c for c in cell_stats(posts, re, "format", now=NOW, thresholds=T)}
    assert cells["carousel"].confidence == "directional"
    assert cells["short_video"].confidence == "established"
    assert cells["carousel"].re_med is not None
    assert len(cells["carousel"].exemplar_post_ids) == 3


def test_trend_needs_five_in_each_window():
    # 30 posts all in the last 28 days -> no prior window -> trend None
    posts = [post(i, 10 + i, days_ago=i % 27 + 1, format="x") for i in range(1, 31)]
    re = compute_re(posts, NOW, T["engagement"])
    [c] = cell_stats(posts, re, "format", now=NOW, thresholds=T)
    assert c.confidence == "established" and c.trend is None
    # now 15 in the last window and 15 in the prior window with the same values -> trend ~ 1
    posts = [post(i, 10, days_ago=i, format="x") for i in range(1, 16)] + \
            [post(100 + i, 10, days_ago=28 + i, format="x") for i in range(1, 16)]
    re = compute_re(posts, NOW, T["engagement"])
    [c] = cell_stats(posts, re, "format", now=NOW, thresholds=T)
    assert c.trend == pytest.approx(1.0)


def test_cross_dimension_cells():
    posts = [post(i, 10, days_ago=i, format="carousel", tone="educational") for i in range(1, 12)] + \
            [post(50 + i, 10, days_ago=i, format="carousel", tone="promotional") for i in range(1, 4)]
    re = compute_re(posts, NOW, T["engagement"])
    cells = cell_stats(posts, re, "format", cross_dimension="tone", now=NOW, thresholds=T)
    by = {(c.label, c.cross_label): c for c in cells}
    assert by[("carousel", "educational")].confidence == "directional"
    assert by[("carousel", "promotional")].confidence == "insufficient"


def test_posts_without_the_dimension_are_excluded_from_share():
    posts = [post(1, 10, 1, format="a"), post(2, 10, 2, format="a"), post(3, 10, 3)]  # third unlabelled
    re = compute_re(posts, NOW, T["engagement"])
    [c] = cell_stats(posts, re, "format", now=NOW, thresholds=T)
    assert c.n == 2 and c.share == 1.0


# --------------------------------------------------------------------------- benchmarks


def test_benchmarks_cadence_mix_and_twitter_caveat():
    a, b = uuid.uuid4(), uuid.uuid4()
    posts = [post(i, 10, days_ago=i * 2, acct=a, format="short_video") for i in range(1, 15)] + \
            [post(100 + i, 10, days_ago=i * 2, acct=b, platform="twitter", format="text_post") for i in range(1, 15)]
    re = compute_re(posts, NOW, T["engagement"])
    bm = {x.account_id: x for x in benchmarks(posts, re, now=NOW, thresholds=T)}
    ig = bm[a]
    assert ig.n == 14 and ig.posts_per_week == pytest.approx(14 / 4.0, abs=0.05)  # 28 days ≈ 4 weeks
    assert ig.format_mix == {"short_video": 1.0}
    assert ig.re_med == 1.0
    tw = bm[b]
    assert tw.posts_per_week is None and tw.caveat


def test_cadence_eligibility_gate():
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    short = [post(i, 10, days_ago=i, acct=a) for i in range(1, 8)]        # one week of history
    re = compute_re(short, NOW, T["engagement"])
    ok, why = cadence_eligible(benchmarks(short, re, now=NOW, thresholds=T), T)
    assert not ok and "accounts" in why
    long = [post(i, 10, days_ago=i * 3, acct=acct) for acct in (a, b, c) for i in range(1, 12)]
    long = [PostObs(**{**p.__dict__, "post_id": n}) for n, p in enumerate(long, start=1)]
    re = compute_re(long, NOW, T["engagement"])
    ok, why = cadence_eligible(benchmarks(long, re, now=NOW, thresholds=T), T)
    assert ok, why
