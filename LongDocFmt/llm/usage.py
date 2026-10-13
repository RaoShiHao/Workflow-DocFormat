"""Normalize LLM usage payloads across OpenAI-compatible providers."""

from __future__ import annotations

from typing import Any


def _int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _dig(obj: Any, *keys: str) -> Any:
    cur = obj
    for key in keys:
        if cur is None:
            return None
        if isinstance(cur, dict):
            cur = cur.get(key)
        else:
            cur = getattr(cur, key, None)
    return cur


def extract_prompt_cached_tokens(usage: Any) -> int | None:
    """Provider prompt-cache hit tokens (subset of input / prompt tokens).

    Compatible with common shapes:
    - OpenAI: ``prompt_tokens_details.cached_tokens``
    - Some gateways: ``input_tokens_details.cached_tokens``
    - DashScope / Qwen-style: ``prompt_cache_hit_tokens`` / top-level ``cached_tokens``
    """
    if usage is None:
        return None
    candidates = (
        _dig(usage, "prompt_tokens_details", "cached_tokens"),
        _dig(usage, "input_tokens_details", "cached_tokens"),
        _dig(usage, "prompt_tokens_details", "cached"),
        _dig(usage, "prompt_cache_hit_tokens"),
        _dig(usage, "cached_tokens"),
        _dig(usage, "cache_read_input_tokens"),
    )
    for value in candidates:
        n = _int(value)
        if n is not None:
            return n
    return None


def normalize_usage(usage: Any) -> dict[str, Any]:
    """Return a flat usage dict; missing optional fields are omitted (not forced to 0)."""
    if usage is None:
        return {}
    prompt = _int(_dig(usage, "prompt_tokens") if not isinstance(usage, dict) else usage.get("prompt_tokens"))
    if prompt is None:
        prompt = _int(_dig(usage, "input_tokens") if not isinstance(usage, dict) else usage.get("input_tokens"))
    completion = _int(
        _dig(usage, "completion_tokens") if not isinstance(usage, dict) else usage.get("completion_tokens")
    )
    if completion is None:
        completion = _int(
            _dig(usage, "output_tokens") if not isinstance(usage, dict) else usage.get("output_tokens")
        )
    total = _int(_dig(usage, "total_tokens") if not isinstance(usage, dict) else usage.get("total_tokens"))
    if total is None and prompt is not None and completion is not None:
        total = prompt + completion
    cached = extract_prompt_cached_tokens(usage)
    out: dict[str, Any] = {}
    if prompt is not None:
        out["prompt_tokens"] = prompt
    if completion is not None:
        out["completion_tokens"] = completion
    if total is not None:
        out["total_tokens"] = total
    # Provider prompt-cache hits remain input tokens; tracked separately for billing/analysis.
    if cached is not None:
        out["prompt_cached_tokens"] = cached
    return out
