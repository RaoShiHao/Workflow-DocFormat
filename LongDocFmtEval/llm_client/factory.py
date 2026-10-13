from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .openai_client import OpenAILanguageModel, OpenAIMultimodalModel
from .zhipu_client import ZhipuLanguageModel, ZhipuMultimodalModel

SUPPORTED_PROVIDERS = frozenset({"openai", "zhipu"})
_PROVIDER_SECTION = {
    "openai": "openai",
    "zhipu": "zhipu",
}


def load_llm_config(path: str | Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _require_api_key(provider_cfg: dict[str, Any], *, section: str) -> str:
    api_key = str(provider_cfg.get("api_key") or "").strip()
    if not api_key:
        raise ValueError(
            f"Missing {section}.api_key in llm_config.yaml "
            f"(direct key only; environment variables are not used)."
        )
    return api_key


def _provider_settings(cfg: dict[str, Any], provider: str) -> tuple[str, str | None]:
    if provider not in _PROVIDER_SECTION:
        raise ValueError(
            f"Unsupported provider={provider!r}. Supported: {sorted(SUPPORTED_PROVIDERS)}"
        )
    section = _PROVIDER_SECTION[provider]
    block = cfg.get(section) or {}
    api_key = _require_api_key(block, section=section)
    base_url = block.get("base_url")
    if base_url is not None:
        base_url = str(base_url).strip() or None
    return api_key, base_url


def _build_language_model(
    *,
    provider: str,
    cfg: dict[str, Any],
    model_cfg: dict[str, Any],
):
    api_key, base_url = _provider_settings(cfg, provider)
    model = model_cfg.get("model")
    default_params = model_cfg.get("default_params") or {}
    if provider == "openai":
        return OpenAILanguageModel(
            api_key=api_key,
            base_url=base_url,
            model=model or "gpt-4o-mini",
            default_params=default_params,
        )
    if provider == "zhipu":
        return ZhipuLanguageModel(
            api_key=api_key,
            base_url=base_url,
            model=model or "glm-4-flash",
            default_params=default_params,
        )
    raise ValueError(f"Unsupported provider={provider!r}")


def _build_multimodal_model(
    *,
    provider: str,
    cfg: dict[str, Any],
    model_cfg: dict[str, Any],
):
    api_key, base_url = _provider_settings(cfg, provider)
    model = model_cfg.get("model")
    default_params = model_cfg.get("default_params") or {}
    if provider == "openai":
        return OpenAIMultimodalModel(
            api_key=api_key,
            base_url=base_url,
            model=model or "gpt-4o-mini",
            default_params=default_params,
        )
    if provider == "zhipu":
        return ZhipuMultimodalModel(
            api_key=api_key,
            base_url=base_url,
            model=model or "glm-4v",
            default_params=default_params,
        )
    raise ValueError(f"Unsupported provider={provider!r}")


def build_models_from_config(path: str | Path):
    """
    Build language + multimodal models from ``config/llm_config.yaml``.

    Each of ``language_model`` / ``multimodal_model`` selects a ``provider``
    (``openai`` | ``zhipu``). API keys are read only from the matching
    ``openai.api_key`` or ``zhipu.api_key`` block — not from environment variables.
    """
    cfg = load_llm_config(path)
    lm_cfg = cfg.get("language_model") or {}
    mm_cfg = cfg.get("multimodal_model") or {}

    lm_provider = str(lm_cfg.get("provider") or "openai").strip().lower()
    mm_provider = str(mm_cfg.get("provider") or lm_provider).strip().lower()

    language_model = _build_language_model(provider=lm_provider, cfg=cfg, model_cfg=lm_cfg)
    multimodal_model = _build_multimodal_model(provider=mm_provider, cfg=cfg, model_cfg=mm_cfg)

    llm_params = {
        "language_model": lm_cfg.get("default_params") or {},
        "multimodal_model": mm_cfg.get("default_params") or {},
    }
    return language_model, multimodal_model, llm_params
