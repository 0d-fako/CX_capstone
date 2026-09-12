"""Spend estimates from token usage. Estimates only; the invoice is the truth."""

from __future__ import annotations

from typing import Any

from smia.settings import load_thresholds


def estimate_usd(usage: dict[str, Any], model: str) -> float:
    prices = (load_thresholds().get("prices_usd_per_mtok") or {}).get(model)
    if not prices:
        return 0.0
    inp = int(usage.get("input_tokens", 0) or 0)
    out = int(usage.get("output_tokens", 0) or 0)
    cr = int(usage.get("cache_read_input_tokens", 0) or 0)
    cw = int(usage.get("cache_creation_input_tokens", 0) or 0)
    usd = (inp * prices["input"] + out * prices["output"] + cr * prices["cache_read"] + cw * prices["cache_write"]) / 1_000_000
    return round(usd, 4)


def usage_of(message: Any) -> dict[str, int]:
    u = getattr(message, "usage", None)
    if not u:
        return {}
    return {k: int(getattr(u, k, 0) or 0) for k in
            ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")}


def add_usage(total: dict[str, Any], one: dict[str, int]) -> None:
    for k, v in one.items():
        total[k] = int(total.get(k, 0) or 0) + v
    total["turns"] = int(total.get("turns", 0) or 0) + 1
