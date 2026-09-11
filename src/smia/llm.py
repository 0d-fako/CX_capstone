"""One place that constructs the Anthropic client from settings (keys live in .env, not
the process environment)."""

from __future__ import annotations

from functools import lru_cache

import anthropic

from smia.settings import get_settings


@lru_cache
def get_client() -> anthropic.Anthropic:
    key = get_settings().anthropic_api_key
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    return anthropic.Anthropic(api_key=key)
