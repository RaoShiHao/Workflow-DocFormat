from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from .openai_client import OpenAILanguageModel, OpenAIMultimodalModel
from .params import EXTRA_KWARGS_CONTAINER_KEYS, STANDARD_API_PARAMS
from .zhipu_client import ZhipuLanguageModel, ZhipuMultimodalModel

SUPPORTED_PROVIDERS = frozenset({"openai", "zhipu"})
# Registry key: lowercase [a-z0-9._-], no spaces or slashes.
MODEL_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
SHARED_SAMPLING_KEYS = frozenset({"temperature", "top_p", "seed"})
_MODEL_IDENTITY_KEYS = frozenset(
    {
        "provider",
        "model",
        "api_key",
        "base_url",
        "vision_model",
        "extra_body",
        "extra_kwargs",
        "kwargs",
        "max_tokens",
        "max_completion_tokens",
    }
)


def load_llm_config(path: str | Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def validate_model_key(key: str) -> str:
    text = str(key or "").strip()
    if not MODEL_KEY_PATTERN.fullmatch(text):
        raise ValueError(
            f"Invalid model key {key!r}. Use lowercase [a-z0-9._-], "
            f"start with a letter or digit, max 64 chars "
            f"(example: qwen3.5-397b-a17b). This key is also the result folder name."
        )
    return text


def list_model_keys(cfg: dict[str, Any]) -> list[str]:
    models = cfg.get("models")
    if not isinstance(models, dict):
        return []
    return [str(k) for k in models.keys()]


def _require_api_key(provider_cfg: dict[str, Any], *, section: str) -> str:
    api_key = str(provider_cfg.get("api_key") or "").strip()
    if not api_key:
        raise ValueError(
            f"Missing {section}.api_key in llm_config.yaml "
            f"(direct key only; environment variables are not used)."
        )
    return api_key


def _provider_settings(cfg: dict[str, Any], provider: str) -> tuple[str, str | None]:
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(
            f"Unsupported provider={provider!r}. Supported: {sorted(SUPPORTED_PROVIDERS)}"
        )
    block = cfg.get(provider) or {}
    api_key = _require_api_key(block, section=provider)
    base_url = block.get("base_url")
    if base_url is not None:
        base_url = str(base_url).strip() or None
    return api_key, base_url


def _credentials(cfg: dict[str, Any], model_cfg: dict[str, Any], *, key: str) -> tuple[str, str, str | None]:
    provider = str(model_cfg.get("provider") or "").strip().lower()
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(
            f"models.{key}.provider must be one of {sorted(SUPPORTED_PROVIDERS)}, got {provider!r}."
        )
    api_key = str(model_cfg.get("api_key") or "").strip()
    base_url = model_cfg.get("base_url")
    fallback = cfg.get(provider) if isinstance(cfg.get(provider), dict) else {}
    if not api_key:
        api_key = str(fallback.get("api_key") or "").strip()
    if base_url is None:
        base_url = fallback.get("base_url")
    if base_url is not None:
        base_url = str(base_url).strip() or None
    if not api_key:
        raise ValueError(
            f"Missing api_key for models.{key} "
            f"(set it on the model entry, or on the {provider}: block)."
        )
    return provider, api_key, base_url


def _model_block(cfg: dict[str, Any], key: str) -> dict[str, Any]:
    models = cfg.get("models")
    if not isinstance(models, dict) or not models:
        raise ValueError("llm_config.yaml has no models: registry.")
    validate_model_key(key)
    block = models.get(key)
    if not isinstance(block, dict):
        available = ", ".join(list_model_keys(cfg)) or "(none)"
        raise ValueError(f"Unknown model key {key!r}. Available: {available}")
    return block


def _shared_params(cfg: dict[str, Any]) -> dict[str, Any]:
    raw = cfg.get("shared_params") or {}
    if not isinstance(raw, dict):
        raise ValueError("shared_params must be a mapping.")
    return dict(raw)


def _default_params(shared: dict[str, Any], model_cfg: dict[str, Any], *, key: str) -> dict[str, Any]:
    """Shared sampling wins; model entries only add extras and optional max_tokens."""
    params = dict(shared)
    for field in ("max_tokens", "max_completion_tokens"):
        if model_cfg.get(field) is not None:
            params[field] = model_cfg[field]
    extra: dict[str, Any] = {}
    for container in EXTRA_KWARGS_CONTAINER_KEYS:
        value = model_cfg.get(container)
        if isinstance(value, dict):
            extra.update(value)
    for name, value in model_cfg.items():
        if name in _MODEL_IDENTITY_KEYS:
            continue
        if name in SHARED_SAMPLING_KEYS:
            raise ValueError(
                f"models.{key} must not set {name}; keep it in shared_params so all models match."
            )
        if name in STANDARD_API_PARAMS:
            params[name] = value
        else:
            extra[name] = value
    if extra:
        existing = params.get("extra_body")
        merged = dict(existing) if isinstance(existing, dict) else {}
        merged.update(extra)
        params["extra_body"] = merged
    return params


def _resolve_registry_keys(
    cfg: dict[str, Any],
    *,
    model: str | None,
    vision: str | None,
) -> tuple[str, str]:
    language_key = str(model or cfg.get("active") or "").strip()
    if not language_key:
        available = ", ".join(list_model_keys(cfg)) or "(none)"
        raise ValueError(
            "Pass model= / --model, or set active: in llm_config.yaml. "
            f"Available keys: {available}"
        )
    vision_key = str(vision or cfg.get("vision") or language_key).strip()
    return validate_model_key(language_key), validate_model_key(vision_key)


def _instantiate_language(
    *,
    provider: str,
    api_key: str,
    base_url: str | None,
    model: str,
    default_params: dict[str, Any],
):
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


def _instantiate_multimodal(
    *,
    provider: str,
    api_key: str,
    base_url: str | None,
    model: str,
    default_params: dict[str, Any],
):
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


def _build_from_registry(
    cfg: dict[str, Any],
    *,
    model: str | None,
    vision: str | None,
):
    language_key, vision_key = _resolve_registry_keys(cfg, model=model, vision=vision)
    shared = _shared_params(cfg)
    language_cfg = _model_block(cfg, language_key)
    vision_cfg = _model_block(cfg, vision_key)
    lm_provider, lm_key, lm_url = _credentials(cfg, language_cfg, key=language_key)
    mm_provider, mm_key, mm_url = _credentials(cfg, vision_cfg, key=vision_key)
    lm_api_id = str(language_cfg.get("model") or language_key).strip()
    mm_api_id = str(
        vision_cfg.get("vision_model") or vision_cfg.get("model") or vision_key
    ).strip()
    lm_params = _default_params(shared, language_cfg, key=language_key)
    mm_params = _default_params(shared, vision_cfg, key=vision_key)
    language_model = _instantiate_language(
        provider=lm_provider,
        api_key=lm_key,
        base_url=lm_url,
        model=lm_api_id,
        default_params=lm_params,
    )
    multimodal_model = _instantiate_multimodal(
        provider=mm_provider,
        api_key=mm_key,
        base_url=mm_url,
        model=mm_api_id,
        default_params=mm_params,
    )
    return language_model, multimodal_model, {
        "language_model": lm_params,
        "multimodal_model": mm_params,
        "model_key": language_key,
        "vision_key": vision_key,
    }


def _build_from_legacy(cfg: dict[str, Any]):
    lm_cfg = cfg.get("language_model") or {}
    mm_cfg = cfg.get("multimodal_model") or {}
    lm_provider = str(lm_cfg.get("provider") or "openai").strip().lower()
    mm_provider = str(mm_cfg.get("provider") or lm_provider).strip().lower()
    lm_api_key, lm_url = _provider_settings(cfg, lm_provider)
    mm_api_key, mm_url = _provider_settings(cfg, mm_provider)
    lm_params = lm_cfg.get("default_params") or {}
    mm_params = mm_cfg.get("default_params") or {}
    language_model = _instantiate_language(
        provider=lm_provider,
        api_key=lm_api_key,
        base_url=lm_url,
        model=str(lm_cfg.get("model") or ""),
        default_params=lm_params,
    )
    multimodal_model = _instantiate_multimodal(
        provider=mm_provider,
        api_key=mm_api_key,
        base_url=mm_url,
        model=str(mm_cfg.get("model") or ""),
        default_params=mm_params,
    )
    return language_model, multimodal_model, {
        "language_model": lm_params,
        "multimodal_model": mm_params,
    }


def build_models_from_config(
    path: str | Path,
    *,
    model: str | None = None,
    vision: str | None = None,
):
    """
    Build language + multimodal models from ``llm_config.yaml``.

    Preferred layout: ``shared_params`` + ``models.<key>`` (lookup by ``model`` /
    ``active``). Legacy ``language_model`` / ``multimodal_model`` is still accepted
    if ``models`` is absent.
    """
    cfg = load_llm_config(path)
    if isinstance(cfg.get("models"), dict) and cfg.get("models"):
        return _build_from_registry(cfg, model=model, vision=vision)
    if model or vision:
        raise ValueError(
            "model=/--model requires a models: registry in llm_config.yaml "
            "(see llm_config.example.yaml)."
        )
    return _build_from_legacy(cfg)
