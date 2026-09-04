from __future__ import annotations

from typing import Any

from zhipuai import ZhipuAI

from .base import BaseLanguageModel, BaseMultimodalModel
from .openai_client import _build_create_params, build_openai_multimodal_images_payload
from .params import merge_llm_params
from .usage import normalize_usage


def _usage_dict(resp: Any) -> dict[str, Any]:
    return normalize_usage(getattr(resp, "usage", None))


class ZhipuLanguageModel(BaseLanguageModel):
    """Text chat via official ``zhipuai`` SDK (``chat.completions``)."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str | None = None,
        client: ZhipuAI | None = None,
        model: str = "glm-4-flash",
        default_params: dict[str, Any] | None = None,
    ) -> None:
        if not str(api_key or "").strip():
            raise ValueError("Zhipu api_key is required (set zhipu.api_key in llm_config.yaml).")
        client_kwargs: dict[str, Any] = {"api_key": api_key.strip()}
        if base_url:
            client_kwargs["base_url"] = base_url
        self.client = client or ZhipuAI(**client_kwargs)
        self.model = model
        self.default_api_params, self.default_extra_body = merge_llm_params(default_params)

    def chat_json(self, *, system: str, user: str, **kwargs: Any) -> dict[str, Any]:
        create_kwargs = _build_create_params(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            default_api_params=self.default_api_params,
            default_extra_body=self.default_extra_body,
            kwargs=kwargs,
        )
        resp = self.client.chat.completions.create(**create_kwargs)
        content = resp.choices[0].message.content or ""
        return {"content": content, "usage": _usage_dict(resp)}


class ZhipuMultimodalModel(BaseMultimodalModel):
    """Vision chat (GLM-4V family) — image payload matches OpenAI ``image_url`` shape."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str | None = None,
        client: ZhipuAI | None = None,
        model: str = "glm-4v",
        default_params: dict[str, Any] | None = None,
    ) -> None:
        if not str(api_key or "").strip():
            raise ValueError("Zhipu api_key is required (set zhipu.api_key in llm_config.yaml).")
        client_kwargs: dict[str, Any] = {"api_key": api_key.strip()}
        if base_url:
            client_kwargs["base_url"] = base_url
        self.client = client or ZhipuAI(**client_kwargs)
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
        create_kwargs = _build_create_params(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": content_items},
            ],
            default_api_params=self.default_api_params,
            default_extra_body=self.default_extra_body,
            kwargs=kwargs,
        )
        resp = self.client.chat.completions.create(**create_kwargs)
        content = resp.choices[0].message.content or ""
        return {"content": content, "usage": _usage_dict(resp)}


__all__ = [
    "ZhipuLanguageModel",
    "ZhipuMultimodalModel",
    "build_openai_multimodal_images_payload",
]
