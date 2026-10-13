"""LLM / image helpers pinned to ``pipeline_auto/config``.

``common.llm`` / ``common.image_gen`` default to ``pipeline_role/config`` via
``common.paths``. This module forces every call from the auto pipeline onto
``pipeline_auto/config/*.yaml`` so the two trees stay independent.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from LongDocFmtEval.common import image_gen as _image_gen
from LongDocFmtEval.common.llm import call_llm_text as _call_llm_text
from LongDocFmtEval.common.llm import stream_llm_text as _stream_llm_text
from LongDocFmtEval.pipeline_auto.auto_paths import (
    LLM_CONFIG_PATH,
    ROLE_LIBRARY_LLM_CONFIG_PATH,
    SEED_LLM_CONFIG_PATH,
)


def llm_config_for(route: str = "role") -> Path:
    r = (route or "role").strip().lower()
    if r in ("role_library", "step1"):
        return ROLE_LIBRARY_LLM_CONFIG_PATH
    if r == "seed":
        return SEED_LLM_CONFIG_PATH
    return LLM_CONFIG_PATH


def stream_llm_text(
    *,
    system: str,
    user: str,
    llm_config: str | Path | None = None,
    route: str = "role",
    temperature: float | None = None,
    max_tokens: int | None = None,
    label: str = "",
    response_format: dict | None = None,
    **kwargs: Any,
) -> str:
    return _stream_llm_text(
        system=system,
        user=user,
        llm_config=llm_config or llm_config_for(route),
        route=route,
        temperature=temperature,
        max_tokens=max_tokens,
        label=label,
        response_format=response_format,
        **kwargs,
    )


def call_llm_text(
    *,
    system: str,
    user: str,
    llm_config: str | Path | None = None,
    route: str = "role",
    temperature: float | None = None,
    max_tokens: int | None = None,
    label: str = "",
    response_format: dict | None = None,
    **kwargs: Any,
) -> str:
    return _call_llm_text(
        system=system,
        user=user,
        llm_config=llm_config or llm_config_for(route),
        route=route,
        temperature=temperature,
        max_tokens=max_tokens,
        label=label,
        response_format=response_format,
        **kwargs,
    )


def generate_image(*args: Any, **kwargs: Any) -> Path:
    kwargs.setdefault("config_path", LLM_CONFIG_PATH)
    return _image_gen.generate_image(*args, **kwargs)


# Re-export helpers used beside generate_image
size_for_aspect = _image_gen.size_for_aspect
is_moderation_error = _image_gen.is_moderation_error
