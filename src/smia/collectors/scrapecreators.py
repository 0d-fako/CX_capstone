"""ScrapeCreators adapter. The only file in the repo that knows this vendor exists.

Endpoints (verified against docs.scrapecreators.com, Sept 2026):
  GET /v1/instagram/profile        ?handle=
  GET /v2/instagram/user/posts     ?handle=&trim=true&next_max_id=
  GET /v1/tiktok/profile           ?handle=
  GET /v3/tiktok/profile/videos    ?handle=&sort_by=latest&trim=true&max_cursor=
  GET /v1/twitter/profile          ?handle=
  GET /v1/twitter/user-tweets      ?handle=&trim=true
Auth: x-api-key header. One credit per request. 404 when a handle does not exist.

Known vendor caveat: the Twitter tweets endpoint returns the user's ~100 most POPULAR
tweets, not the latest. Cadence claims on X are therefore unreliable and the ingestion
layer records that on the capture.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

from smia.collectors.base import (
    CollectResult,
    HandleInfo,
    Metrics,
    Platform,
    RawCapture,
    TargetSpec,
)

log = logging.getLogger(__name__)

BASE_URL = "https://api.scrapecreators.com"


class VendorError(RuntimeError):
    """Transport failure or 5xx; retried once."""


class NotFound(LookupError):
    """The handle does not exist on the platform; never retried."""


def _int(v: Any, default: int | None = 0) -> int | None:
    try:
        return int(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _get(d: Any, *path: str, default: Any = None) -> Any:
    cur = d
    for p in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(p)
    return default if cur is None else cur


# --------------------------------------------------------------------------- parsers
# Pure functions from one vendor item to one RawCapture. Tested without network.

IG_MEDIA_TYPES = {1: "image", 2: "video", 8: "carousel"}


def parse_instagram_item(item: dict[str, Any], handle: str) -> RawCapture | None:
    pk = item.get("pk") or item.get("id")
    if not pk:
        return None
    ts = item.get("taken_at")
    if ts is None:
        return None
    posted_at = datetime.fromtimestamp(int(ts), tz=UTC)
    media_type = IG_MEDIA_TYPES.get(_int(item.get("media_type"), None) or -1)
    duration = item.get("video_duration")
    code = item.get("code")
    url = item.get("url") or (f"https://www.instagram.com/p/{code}/" if code else None)
    return RawCapture(
        platform="instagram",
        handle=handle,
        post_id=str(pk),
        posted_at=posted_at,
        content=_get(item, "caption", "text") or None,
        media_type=media_type,
        media_duration_s=round(float(duration)) if duration else None,
        url=url,
        metrics=Metrics(
            likes=_int(item.get("like_count")) or 0,
            comments=_int(item.get("comment_count")) or 0,
            shares=0,  # not exposed by the platform
            views=_int(item.get("play_count") or item.get("ig_play_count"), None),
        ),
        raw_json=item,
    )


def parse_tiktok_item(item: dict[str, Any], handle: str) -> RawCapture | None:
    aweme_id = item.get("aweme_id")
    ts = item.get("create_time")
    if not aweme_id or ts is None:
        return None
    duration_ms = _get(item, "video", "duration")
    stats = item.get("statistics") or {}
    return RawCapture(
        platform="tiktok",
        handle=handle,
        post_id=str(aweme_id),
        posted_at=datetime.fromtimestamp(int(ts), tz=UTC),
        content=item.get("desc") or None,
        media_type="video",
        media_duration_s=round(int(duration_ms) / 1000) if duration_ms else None,
        url=item.get("share_url") or f"https://www.tiktok.com/@{handle.lstrip('@')}/video/{aweme_id}",
        metrics=Metrics(
            likes=_int(stats.get("digg_count")) or 0,
            comments=_int(stats.get("comment_count")) or 0,
            shares=_int(stats.get("share_count")) or 0,
            views=_int(stats.get("play_count"), None),
        ),
        raw_json=item,
    )


def _twitter_media_type(legacy: dict[str, Any]) -> str:
    media = _get(legacy, "entities", "media", default=[]) or []
    types = {m.get("type") for m in media if isinstance(m, dict)}
    if "video" in types or "animated_gif" in types:
        return "video"
    if "photo" in types:
        return "carousel" if len(media) > 1 else "image"
    if _get(legacy, "entities", "urls", default=[]):
        return "link"
    return "text"


def parse_twitter_item(item: dict[str, Any], handle: str) -> RawCapture | None:
    legacy = item.get("legacy") or {}
    tweet_id = item.get("rest_id") or legacy.get("id_str")
    created = legacy.get("created_at")
    if not tweet_id or not created:
        return None
    try:
        posted_at = parsedate_to_datetime(created)  # "Wed Oct 10 20:19:24 +0000 2018"
    except (TypeError, ValueError):
        return None
    if posted_at.tzinfo is None:
        posted_at = posted_at.replace(tzinfo=UTC)
    views = _int(_get(item, "views", "count"), None)
    url = item.get("url") or f"https://x.com/{handle}/status/{tweet_id}"
    duration_s = None
    for m in _get(legacy, "extended_entities", "media", default=[]) or []:
        ms = _get(m, "video_info", "duration_millis") if isinstance(m, dict) else None
        if ms:
            duration_s = round(int(ms) / 1000)
            break
    return RawCapture(
        platform="twitter",
        handle=handle,
        post_id=str(tweet_id),
        posted_at=posted_at,
        content=legacy.get("full_text") or None,
        media_type=_twitter_media_type(legacy),  # type: ignore[arg-type]
        media_duration_s=duration_s,  # from extended_entities when present
        url=url,
        metrics=Metrics(
            likes=_int(legacy.get("favorite_count")) or 0,
            comments=_int(legacy.get("reply_count")) or 0,
            shares=(_int(legacy.get("retweet_count")) or 0) + (_int(legacy.get("quote_count")) or 0),
            views=views,
        ),
        raw_json=item,
    )


def parse_instagram_profile(body: dict[str, Any], handle: str) -> HandleInfo | None:
    user = _get(body, "data", "user")
    if not isinstance(user, dict):
        return None
    return HandleInfo(
        platform="instagram",
        handle=user.get("username") or handle,
        display_name=user.get("full_name"),
        follower_count=_int(_get(user, "edge_followed_by", "count"), None),
        post_count=_int(_get(user, "edge_owner_to_timeline_media", "count"), None),
        platform_user_id=str(user.get("id")) if user.get("id") else None,
        is_private=bool(user.get("is_private", False)),
    )


def parse_tiktok_profile(body: dict[str, Any], handle: str) -> HandleInfo | None:
    user = body.get("user") if isinstance(body.get("user"), dict) else body
    stats = body.get("stats") or _get(body, "user", "stats") or {}
    unique = user.get("uniqueId") or body.get("uniqueId")
    if not unique and not user.get("id"):
        return None
    return HandleInfo(
        platform="tiktok",
        handle=unique or handle,
        display_name=user.get("nickname") or body.get("nickname"),
        follower_count=_int(stats.get("followerCount"), None),
        post_count=_int(stats.get("videoCount"), None),
        platform_user_id=str(user.get("id")) if user.get("id") else None,
        is_private=bool(user.get("privateAccount", False)),
    )


def parse_twitter_profile(body: dict[str, Any], handle: str) -> HandleInfo | None:
    legacy = body.get("legacy") or _get(body, "data", "legacy") or {}
    if not legacy:
        return None
    return HandleInfo(
        platform="twitter",
        handle=legacy.get("screen_name") or handle,
        display_name=legacy.get("name"),
        follower_count=_int(legacy.get("followers_count"), None),
        post_count=_int(legacy.get("statuses_count"), None),
        platform_user_id=str(body.get("rest_id")) if body.get("rest_id") else None,
        is_private=bool(legacy.get("protected", False)),
    )


# --------------------------------------------------------------------------- client

_POSTS = {
    "instagram": ("/v2/instagram/user/posts", "items", parse_instagram_item),
    "tiktok": ("/v3/tiktok/profile/videos", "aweme_list", parse_tiktok_item),
    "twitter": ("/v1/twitter/user-tweets", "tweets", parse_twitter_item),
}
_PROFILE = {
    "instagram": ("/v1/instagram/profile", parse_instagram_profile),
    "tiktok": ("/v1/tiktok/profile", parse_tiktok_profile),
    "twitter": ("/v1/twitter/profile", parse_twitter_profile),
}


class ScrapeCreatorsCollector:
    """One page of recent posts per target per call: one credit per request."""

    def __init__(self, api_key: str, *, timeout_s: float = 30.0, client: httpx.Client | None = None):
        self._client = client or httpx.Client(
            base_url=BASE_URL, headers={"x-api-key": api_key}, timeout=timeout_s
        )
        self.credits_used = 0

    @retry(
        reraise=True,
        stop=stop_after_attempt(2),
        wait=wait_fixed(2),
        retry=retry_if_exception_type(VendorError),
    )
    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            r = self._client.get(path, params=params)
        except httpx.TransportError as e:
            raise VendorError(str(e)) from e
        if r.status_code == 404:
            raise NotFound(f"{path} {params.get('handle')}")
        if r.status_code >= 500:
            raise VendorError(f"{r.status_code} from {path}")
        if r.status_code >= 400:
            raise RuntimeError(f"{r.status_code} from {path}: {r.text[:200]}")
        body = r.json()
        self.credits_used += _int(body.get("credits_charged"), 1) or 1
        if body.get("success") is False:
            raise RuntimeError(f"vendor returned success=false for {path}: {str(body)[:200]}")
        return body

    def resolve_handle(self, platform: Platform, handle: str) -> HandleInfo | None:
        path, parser = _PROFILE[platform]
        try:
            body = self._get(path, {"handle": handle.lstrip("@")})
        except NotFound:
            return None
        return parser(body, handle)

    def collect(self, target: TargetSpec) -> CollectResult:
        path, list_key, parser = _POSTS[target.platform]
        params: dict[str, Any] = {"handle": target.handle.lstrip("@"), "trim": "true"}
        if target.platform == "tiktok":
            params["sort_by"] = "latest"
        try:
            body = self._get(path, params)
        except NotFound:
            return CollectResult(captures=[], credits_used=1)
        items = body.get(list_key) or []
        captures: list[RawCapture] = []
        skipped = 0
        for item in items:
            try:
                cap = parser(item, target.handle) if isinstance(item, dict) else None
            except Exception as e:  # noqa: BLE001 - a bad item never kills the batch
                log.warning("skip %s item on %s: %s", target.platform, target.handle, e)
                cap = None
            if cap is None:
                skipped += 1
                continue
            captures.append(cap)
        return CollectResult(
            captures=captures, credits_used=_int(body.get("credits_charged"), 1) or 1, skipped_items=skipped
        )
