"""Tests for provider usage normalization (prompt cache fields)."""

from __future__ import annotations

from LongDocFormatter.llm.usage import extract_prompt_cached_tokens, normalize_usage


def test_normalize_openai_prompt_tokens_details() -> None:
    usage = {
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "total_tokens": 120,
        "prompt_tokens_details": {"cached_tokens": 40},
    }
    out = normalize_usage(usage)
    assert out["prompt_tokens"] == 100
    assert out["completion_tokens"] == 20
    assert out["total_tokens"] == 120
    assert out["prompt_cached_tokens"] == 40
    assert extract_prompt_cached_tokens(usage) == 40


def test_normalize_dashscope_style_hit_tokens() -> None:
    usage = {
        "prompt_tokens": 80,
        "completion_tokens": 10,
        "total_tokens": 90,
        "prompt_cache_hit_tokens": 25,
    }
    out = normalize_usage(usage)
    assert out["prompt_cached_tokens"] == 25


def test_normalize_object_attrs() -> None:
    class Details:
        cached_tokens = 7

    class Usage:
        prompt_tokens = 50
        completion_tokens = 5
        total_tokens = 55
        prompt_tokens_details = Details()

    out = normalize_usage(Usage())
    assert out["prompt_tokens"] == 50
    assert out["prompt_cached_tokens"] == 7
