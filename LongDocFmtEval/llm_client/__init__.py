"""Vendored LLM client factory (formerly format_migration.llm).

Kept inside LongDocFmtEval so the tree can run without the monorepo
``format_migration`` package.
"""
from __future__ import annotations

from .factory import build_models_from_config, load_llm_config
from .json_util import safe_json_loads
from .params import merge_llm_params, split_llm_params

__all__ = [
    "build_models_from_config",
    "load_llm_config",
    "merge_llm_params",
    "split_llm_params",
    "safe_json_loads",
]
