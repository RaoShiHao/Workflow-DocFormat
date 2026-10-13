from __future__ import annotations

import base64
import os
from typing import Any

from openai import OpenAI

from .base import BaseLanguageModel, BaseMultimodalModel
from .params import merge_llm_params
from .usage import normalize_usage


def _file_to_data_url(path: str) -> str:
    ext = os.path.splitext(path)[1].lower().lstrip(".") or "png"
    mime = "image/png" if ext in ("png", "apng") else f"image/{ext}"
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")
    return f"data:{mime};base64,{b64}"


def _build_create_params(
    *,
    model: str,
    messages: list[dict[str, Any]],
    default_api_params: dict[str, Any],
    default_extra_body: dict[str, Any],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    call_api, call_extra = merge_llm_params(kwargs)
    api_params = {**default_api_params, **call_api}
    extra_body = {**default_extra_body, **call_extra}
    params: dict[str, Any] = {
        "model": model,
        "messages": messages,
        **api_params,
    }
    if extra_body:
        params["extra_body"] = extra_body
    return params


class OpenAILanguageModel(BaseLanguageModel):
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str | None = None,
        client: OpenAI | None = None,
        model: str = "gpt-4o-mini",
        default_params: dict[str, Any] | None = None,
    ) -> None:
        if not str(api_key or "").strip():
            raise ValueError("OpenAI api_key is required (set openai.api_key in llm_config.yaml).")
        self.client = client or OpenAI(
            api_key=api_key.strip(),
            base_url=base_url,
        )
        self.model = model
        self.default_api_params, self.default_extra_body = merge_llm_params(default_params)

    def chat_json(self, *, system: str, user: str, **kwargs: Any) -> dict[str, Any]:
        params = _build_create_params(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            default_api_params=self.default_api_params,
            default_extra_body=self.default_extra_body,
            kwargs=kwargs,
        )
        resp = self.client.chat.completions.create(**params)
        if isinstance(resp, str):
            raise RuntimeError(
                "OpenAI-compatible API returned a string instead of ChatCompletion: "
                f"{resp[:800]}"
            )
        content = resp.choices[0].message.content or ""
        return {"content": content, "usage": normalize_usage(getattr(resp, "usage", None))}


class OpenAIMultimodalModel(BaseMultimodalModel):
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str | None = None,
        client: OpenAI | None = None,
        model: str = "gpt-4o-mini",
        default_params: dict[str, Any] | None = None,
    ) -> None:
        if not str(api_key or "").strip():
            raise ValueError("OpenAI api_key is required (set openai.api_key in llm_config.yaml).")
        self.client = client or OpenAI(
            api_key=api_key.strip(),
            base_url=base_url,
        )
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
        params = _build_create_params(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": content_items},
            ],
            default_api_params=self.default_api_params,
            default_extra_body=self.default_extra_body,
            kwargs=kwargs,
        )
        resp = self.client.chat.completions.create(**params)
        content = resp.choices[0].message.content or ""
        return {"content": content, "usage": normalize_usage(getattr(resp, "usage", None))}


def build_openai_multimodal_images_payload(image_paths: list[str]) -> list[dict[str, Any]]:
    return [
        {"type": "image_url", "image_url": {"url": _file_to_data_url(p)}}
        for p in image_paths
    ]
