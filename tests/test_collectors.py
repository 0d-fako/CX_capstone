"""Parsers against the documented vendor response shapes. No network."""

from __future__ import annotations

from datetime import UTC

import httpx
import pytest

from smia.collectors.base import TargetSpec
from smia.collectors.scrapecreators import (
    ScrapeCreatorsCollector,
    VendorError,
    parse_instagram_item,
    parse_instagram_profile,
    parse_tiktok_item,
    parse_tiktok_profile,
    parse_twitter_item,
    parse_twitter_profile,
)

IG_ITEM = {
    "pk": "3141592653", "code": "CxYz12", "taken_at": 1725000000,
    "caption": {"text": "New drop this Friday"}, "media_type": 2,
    "like_count": 120, "comment_count": 8, "play_count": 4300, "video_duration": 94.6,
    "url": "https://www.instagram.com/p/CxYz12/",
}
TT_ITEM = {
    "aweme_id": "7301", "desc": "POV: payday", "create_time": 1725000000,
    "video": {"duration": 15400}, "share_url": "https://www.tiktok.com/@x/video/7301",
    "statistics": {"play_count": 99000, "digg_count": 5000, "comment_count": 120, "share_count": 300},
}
TW_ITEM = {
    "rest_id": "18000", "url": "https://x.com/acme/status/18000",
    "legacy": {
        "created_at": "Wed Oct 10 20:19:24 +0000 2018", "full_text": "We raised.",
        "favorite_count": 40, "reply_count": 3, "retweet_count": 5, "quote_count": 2,
        "entities": {"media": [{"type": "photo"}, {"type": "photo"}]},
    },
    "views": {"count": "1200"},
}


def test_instagram_item_maps_to_envelope():
    cap = parse_instagram_item(IG_ITEM, "acme")
    assert cap and cap.post_id == "3141592653"
    assert cap.posted_at.tzinfo is UTC
    assert cap.media_type == "video" and cap.media_duration_s == 95
    assert cap.metrics.likes == 120 and cap.metrics.views == 4300 and cap.metrics.shares == 0
    assert cap.content == "New drop this Friday"
    assert cap.empty_fields() == []


def test_tiktok_item_duration_is_seconds():
    cap = parse_tiktok_item(TT_ITEM, "acme")
    assert cap and cap.media_duration_s == 15
    assert cap.metrics.shares == 300 and cap.metrics.views == 99000


def test_tiktok_url_constructed_when_trimmed_response_omits_it():
    item = {**TT_ITEM}
    del item["share_url"]
    cap = parse_tiktok_item(item, "@acme")
    assert cap and cap.url == "https://www.tiktok.com/@acme/video/7301"


def test_twitter_video_duration_from_extended_entities():
    item = {**TW_ITEM, "legacy": {**TW_ITEM["legacy"],
            "entities": {"media": [{"type": "video"}]},
            "extended_entities": {"media": [{"type": "video", "video_info": {"duration_millis": 95400}}]}}}
    cap = parse_twitter_item(item, "acme")
    assert cap and cap.media_type == "video" and cap.media_duration_s == 95


def test_twitter_item_shares_and_media():
    cap = parse_twitter_item(TW_ITEM, "acme")
    assert cap and cap.post_id == "18000"
    assert cap.metrics.shares == 7  # retweets + quotes
    assert cap.media_type == "carousel"
    assert cap.posted_at.year == 2018


@pytest.mark.parametrize("bad", [{}, {"pk": "1"}, {"taken_at": 1}])
def test_bad_instagram_item_returns_none(bad):
    assert parse_instagram_item(bad, "acme") is None


def test_profiles():
    ig = parse_instagram_profile(
        {"success": True, "data": {"user": {"username": "acme", "full_name": "Acme", "id": "9",
         "edge_followed_by": {"count": 1500}, "edge_owner_to_timeline_media": {"count": 210},
         "is_private": False}}}, "acme")
    assert ig and ig.follower_count == 1500 and ig.post_count == 210
    tt = parse_tiktok_profile(
        {"user": {"id": "77", "uniqueId": "acme", "nickname": "Acme"},
         "stats": {"followerCount": 8000, "videoCount": 40}}, "acme")
    assert tt and tt.follower_count == 8000 and tt.platform_user_id == "77"
    tw = parse_twitter_profile(
        {"rest_id": "5", "legacy": {"screen_name": "acme", "name": "Acme",
         "followers_count": 300, "statuses_count": 900}}, "acme")
    assert tw and tw.post_count == 900


def _collector(handler):
    transport = httpx.MockTransport(handler)
    client = httpx.Client(base_url="https://api.scrapecreators.com", transport=transport,
                          headers={"x-api-key": "k"})
    return ScrapeCreatorsCollector("k", client=client)


def test_collect_skips_bad_items_and_counts_credits():
    def handler(req: httpx.Request):
        assert req.headers["x-api-key"] == "k"
        assert req.url.params["handle"] == "acme"
        return httpx.Response(200, json={"success": True, "credits_charged": 1,
                                         "items": [IG_ITEM, {"garbage": True}, "not a dict"]})
    c = _collector(handler)
    res = c.collect(TargetSpec("instagram", "@acme"))
    assert len(res.captures) == 1 and res.skipped_items == 2
    assert res.credits_used == 1 and c.credits_used == 1


def test_resolve_handle_not_found_returns_none_without_retry():
    calls = {"n": 0}
    def handler(req):
        calls["n"] += 1
        return httpx.Response(404, json={"success": False})
    c = _collector(handler)
    assert c.resolve_handle("twitter", "nobody_here_xyz") is None
    assert calls["n"] == 1


def test_server_error_retries_once_then_raises():
    calls = {"n": 0}
    def handler(req):
        calls["n"] += 1
        return httpx.Response(503, text="down")
    c = _collector(handler)
    with pytest.raises(VendorError):
        c.collect(TargetSpec("tiktok", "acme"))
    assert calls["n"] == 2
