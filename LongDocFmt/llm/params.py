"""Parse ``default_params`` / call kwargs into API params vs provider ``extra_body``."""

from __future__ import annotations

from typing import Any

# Known OpenAI-compatible chat params → top-level ``create(**api_params)``.
STANDARD_API_PARAMS = frozenset(
    {
        "temperature",
        "max_tokens",
        "max_completion_tokens",
        "top_p",
        "frequency_penalty",
        "presence_penalty",
        "stop",
        "n",
        "seed",
        "response_format",
        "logprobs",
        "top_logprobs",
        "stream",
        "tools",
        "tool_choice",
        "parallel_tool_calls",
    }
)

# Optional nested block in YAML; contents merge into ``extra_body`` (**kwargs).
EXTRA_KWARGS_CONTAINER_KEYS = frozenset({"extra_kwargs", "extra_body", "kwargs"})


def split_llm_params(params: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Split ``default_params`` like ``create(*, temperature=..., **extra_kwargs)``.

    - Keys in ``STANDARD_API_PARAMS`` → top-level API params.
    - All other top-level keys → ``extra_body`` (provider-specific extensions).
    - Nested ``extra_kwargs`` / ``extra_body`` / ``kwargs`` dict → merged into ``extra_body``.
    """
    if not params:
        return {}, {}

    api_params: dict[str, Any] = {}
    extra_kwargs: dict[str, Any] = {}

    for key, value in params.items():
        if key in EXTRA_KWARGS_CONTAINER_KEYS:
            if isinstance(value, dict):
                extra_kwargs.update(value)
            continue

        if key in STANDARD_API_PARAMS:
            api_params[key] = value
        else:
            extra_kwargs[key] = value

    return api_params, extra_kwargs


def merge_llm_params(
    *layers: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Merge multiple param dicts; later layers override earlier ones."""
    api: dict[str, Any] = {}
    extra: dict[str, Any] = {}
    for layer in layers:
        layer_api, layer_extra = split_llm_params(layer)
        api.update(layer_api)
        extra.update(layer_extra)
    return api, extra
