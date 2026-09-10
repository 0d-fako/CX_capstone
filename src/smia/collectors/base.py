"""The collector contract. Every source, bought or built, implements this and nothing above
this layer knows which vendor produced the data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

Platform = Literal["instagram", "tiktok", "twitter"]
MediaType = Literal["video", "image", "carousel", "text", "link"]


@dataclass(frozen=True)
class TargetSpec:
    """What a collector needs to know about a target. Deliberately not the ORM row."""

    platform: Platform
    handle: str


class Metrics(BaseModel):
    likes: int = 0
    comments: int = 0
    shares: int = 0
    views: int | None = None


class RawCapture(BaseModel):
    """One post as observed at collection time, in our shape, not the vendor's."""

    platform: Platform
    handle: str
    post_id: str
    posted_at: datetime
    content: str | None = None
    media_type: MediaType | None = None
    media_duration_s: int | None = None
    url: str | None = None
    metrics: Metrics = Field(default_factory=Metrics)
    raw_json: dict[str, Any] = Field(default_factory=dict)

    def empty_fields(self) -> list[str]:
        """Which envelope fields the vendor left blank. Used by the smoke test."""
        out = []
        for name in ("content", "media_type", "url"):
            if getattr(self, name) in (None, ""):
                out.append(name)
        if self.media_type == "video" and self.media_duration_s is None:
            out.append("media_duration_s")
        m = self.metrics
        if m.likes == 0 and m.comments == 0 and m.shares == 0 and not m.views:
            out.append("metrics")
        return out


class HandleInfo(BaseModel):
    """Result of verifying that an account exists. One vendor credit."""

    platform: Platform
    handle: str
    display_name: str | None = None
    follower_count: int | None = None
    post_count: int | None = None
    platform_user_id: str | None = None
    is_private: bool = False


class CollectResult(BaseModel):
    captures: list[RawCapture]
    credits_used: int = 0
    skipped_items: int = 0


class Collector(Protocol):
    def collect(self, target: TargetSpec) -> CollectResult: ...

    def resolve_handle(self, platform: Platform, handle: str) -> HandleInfo | None: ...
