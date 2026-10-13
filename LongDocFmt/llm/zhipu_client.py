from __future__ import annotations

from typing import Any

from zai import ZhipuAiClient

from .base import BaseLanguageModel, BaseMultimodalModel
from .openai_client import _build_create_params, build_openai_multimodal_images_payload
from .params import merge_llm_params
from .usage import normalize_usage

# zai-sdk accepts these as top-level create() kwargs (not nested extra_body).
# ``reasoning_effort`` is API-top-level in docs but not a named SDK arg → keep in extra_body.
_ZHIPU_TOP_LEVEL_EXTRA = frozenset(
    {
        "thinking",
        "do_sample",
        "sensitive_word_check",
        "tool_stream",
        "watermark_enabled",
        "request_id",
        "user_id",
        "meta",
    }
)

# GLM-5.3 / 5.3-flash: thinking.type=disabled is rejected; use low effort instead.
_FORCE_THINKING_PREFIXES = ("glm-5.3", "glm-5-3", "glm-5p3")


def _usage_dict(resp: Any) -> dict[str, Any]:
    return normalize_usage(getattr(resp, "usage", None))


def _model_forces_thinking(model: str) -> bool:
    mid = str(model or "").strip().lower().replace("_", "-")
    return any(mid == p or mid.startswith(f"{p}-") or mid.startswith(f"{p}.") for p in _FORCE_THINKING_PREFIXES)


def _normalize_glm53_thinking(create_kwargs: dict[str, Any]) -> None:
    """Rewrite illegal disabled-thinking for GLM-5.3 → enabled + reasoning_effort=low."""
    if not _model_forces_thinking(str(create_kwargs.get("model") or "")):
        return
    thinking = create_kwargs.get("thinking")
    if not isinstance(thinking, dict):
        return
    if str(thinking.get("type") or "").strip().lower() != "disabled":
        return
    create_kwargs["thinking"] = {**thinking, "type": "enabled"}
    extra = dict(create_kwargs.get("extra_body") or {})
    extra.setdefault("reasoning_effort", "low")
    create_kwargs["extra_body"] = extra


def _finalize_zai_create_kwargs(create_kwargs: dict[str, Any]) -> dict[str, Any]:
    """Promote zai-native keys out of ``extra_body`` into top-level kwargs."""
    extra = dict(create_kwargs.pop("extra_body", None) or {})
    for key in list(extra):
        if key in _ZHIPU_TOP_LEVEL_EXTRA:
            create_kwargs[key] = extra.pop(key)
    if extra:
        create_kwargs["extra_body"] = extra
    _normalize_glm53_thinking(create_kwargs)
    return create_kwargs


class ZhipuLanguageModel(BaseLanguageModel):
    """Text chat via official ``zai-sdk`` (``ZhipuAiClient.chat.completions``).

    Required for GLM-5.x (e.g. ``glm-5.3``). Install: ``pip install zai-sdk``.
    Domestic endpoint defaults inside the SDK; optional ``base_url`` override is supported.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str | None = None,
        client: ZhipuAiClient | None = None,
        model: str = "glm-5.3",
        default_params: dict[str, Any] | None = None,
    ) -> None:
        if not str(api_key or "").strip():
            raise ValueError("Zhipu api_key is required (set zhipu.api_key in llm_config.yaml).")
        client_kwargs: dict[str, Any] = {"api_key": api_key.strip()}
        if base_url:
            client_kwargs["base_url"] = base_url
        self.client = client or ZhipuAiClient(**client_kwargs)
        self.model = model
        self.default_api_params, self.default_extra_body = merge_llm_params(default_params)

    def chat_json(self, *, system: str, user: str, **kwargs: Any) -> dict[str, Any]:
        create_kwargs = _finalize_zai_create_kwargs(
            _build_create_params(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                default_api_params=self.default_api_params,
                default_extra_body=self.default_extra_body,
                kwargs=kwargs,
            )
        )
        resp = self.client.chat.completions.create(**create_kwargs)
        content = resp.choices[0].message.content or ""
        return {"content": content, "usage": _usage_dict(resp)}


class ZhipuMultimodalModel(BaseMultimodalModel):
    """Vision chat via ``zai-sdk`` using OpenAI-style ``image_url`` content blocks.

    Must use a VLM id (e.g. ``glm-5.3-flash``, ``glm-4.6v``). Text-only ids such as
    ``glm-5.3`` reject ``type=image_url`` with error 1210 (only ``text`` allowed).
    The factory maps text Zhipu models to a default VLM via ``vision_model``.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str | None = None,
        client: ZhipuAiClient | None = None,
        model: str = "glm-5.3-flash",
        default_params: dict[str, Any] | None = None,
    ) -> None:
        if not str(api_key or "").strip():
            raise ValueError("Zhipu api_key is required (set zhipu.api_key in llm_config.yaml).")
        client_kwargs: dict[str, Any] = {"api_key": api_key.strip()}
        if base_url:
            client_kwargs["base_url"] = base_url
        self.client = client or ZhipuAiClient(**client_kwargs)
        self.model = model
        self.default_api_params, self.default_extra_body = merge_llm_params(default_params)

    def chat_json(
        self,
        *,
        system: str,
        user: str,
        images: list[dict[str, Any]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        content_items: list[dict[str, Any]] = [{"type": "text", "text": user}]
        content_items.extend(images)
        create_kwargs = _finalize_zai_create_kwargs(
            _build_create_params(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": content_items},
                ],
                default_api_params=self.default_api_params,
                default_extra_body=self.default_extra_body,
                kwargs=kwargs,
            )
        )
        resp = self.client.chat.completions.create(**create_kwargs)
        content = resp.choices[0].message.content or ""
        return {"content": content, "usage": _usage_dict(resp)}


__all__ = [
    "ZhipuLanguageModel",
    "ZhipuMultimodalModel",
    "build_openai_multimodal_images_payload",
]
