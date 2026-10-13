from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from LongDocFmtEval.llm_client import build_models_from_config, safe_json_loads
from .paths import LLM_CONFIG_PATH, ROLE_LIBRARY_LLM_CONFIG_PATH, SEED_LLM_CONFIG_PATH


def _config_has_usable_key(path: Path) -> bool:
    """True if yaml exists and its language_model provider block has a non-empty api_key."""
    if not path.exists():
        return False
    try:
        cfg = load_yaml(path)
    except Exception:  # noqa: BLE001
        return False
    lm = cfg.get("language_model", {}) or {}
    provider = str(lm.get("provider") or "openai").strip() or "openai"
    block = cfg.get(provider) or cfg.get("openai") or {}
    return bool(str(block.get("api_key") or "").strip())


def resolve_llm_config_path(
    llm_config: str | Path | None = None,
    *,
    route: str = "role",
) -> Path:
    """Resolve LLM yaml path.

    route:
      - ``role``: default pipeline (step0/2/3…) → ``llm_config.yaml``
      - ``seed``: seed tooling → ``seed_llm_config.yaml`` (fallback role)
      - ``role_library`` / ``step1``: Role 库长生成 → ``role_library_llm_config.yaml``
        （未配置可用 key 时回退 ``llm_config.yaml``）
    """
    if llm_config:
        return Path(llm_config)
    r = (route or "role").strip().lower()
    if r == "seed":
        return SEED_LLM_CONFIG_PATH
    if r in ("role_library", "step1"):
        if _config_has_usable_key(ROLE_LIBRARY_LLM_CONFIG_PATH):
            return ROLE_LIBRARY_LLM_CONFIG_PATH
        return LLM_CONFIG_PATH
    return LLM_CONFIG_PATH


def _openai_compatible_client(cfg: dict[str, Any]):
    """Build OpenAI client from language_model.provider's api_key/base_url block."""
    from openai import OpenAI

    lm = cfg.get("language_model", {}) or {}
    provider = str(lm.get("provider") or "openai").strip() or "openai"
    block = cfg.get(provider) or cfg.get("openai") or {}
    return OpenAI(
        api_key=str(block.get("api_key") or "").strip(),
        base_url=block.get("base_url"),
    )


def load_yaml(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def dump_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def render_template(template: str, **kwargs: Any) -> str:
    text = template
    for key, value in kwargs.items():
        token = "{{" + key + "}}"
        if isinstance(value, (dict, list)):
            rendered = json.dumps(value, ensure_ascii=False, indent=2)
        else:
            rendered = str(value)
        text = text.replace(token, rendered)
    return text


def load_prompt(path: Path) -> dict[str, str]:
    data = load_yaml(path)
    block = data.get("prompt") or data
    system = block.get("system")
    user_template = block.get("user_template")
    if not system or not user_template:
        raise ValueError(f"Prompt missing system/user_template: {path}")
    return {"system": str(system), "user_template": str(user_template)}


def _llm_call_kwargs(
    *,
    llm_config: str | Path | None,
    route: str,
    temperature: float | None,
    max_tokens: int | None,
    response_format: dict[str, Any] | None = None,
) -> tuple[Any, str, dict[str, Any]]:
    """Shared client + create kwargs for chat completions.

    Returns ``(client, model, create_kwargs)``.
    ``default_params`` 里除 temperature/max_tokens/extra_body 外的键
   （如 ``reasoning_effort``）会作为顶层参数传给 API。
    """
    cfg_path = resolve_llm_config_path(llm_config, route=route)
    cfg = load_yaml(cfg_path)
    lm = cfg.get("language_model", {})
    default_params = dict(lm.get("default_params", {}) or {})
    client = _openai_compatible_client(cfg)
    model = str(lm.get("model") or "gpt-5.2")
    temp = temperature if temperature is not None else default_params.get("temperature", 0.4)
    # max_tokens: 显式传入优先；yaml 写 null/省略键可表示「不传给 API，走中转默认」
    if max_tokens is not None:
        tok = max_tokens
    elif "max_tokens" not in default_params:
        tok = None  # omit → 让中转/模型默认决定（不一定更大）
    else:
        tok = default_params.get("max_tokens")
    extra = default_params.get("extra_body") or None
    # 空 dict / 仅含空值时不要传 extra_body，避免部分网关报 unknown_parameter
    if isinstance(extra, dict) and not any(v is not None and v != "" for v in extra.values()):
        extra = None

    create_kwargs: dict[str, Any] = {
        "temperature": temp,
    }
    # 仅当有正整数时才带 max_tokens；0/null 表示不限制（由中转默认）
    if tok is not None and int(tok) > 0:
        create_kwargs["max_tokens"] = int(tok)
    if extra is not None:
        create_kwargs["extra_body"] = extra
    # OpenAI 关/降思考：reasoning_effort = none|minimal|low|medium|high …
    # 勿用 enable_thinking（非官方字段）
    reserved = {"temperature", "max_tokens", "extra_body"}
    for key, value in default_params.items():
        if key in reserved or value is None or value == "":
            continue
        create_kwargs[key] = value
    if response_format:
        create_kwargs["response_format"] = response_format
    return client, model, create_kwargs


def _unsupported_chat_param(exc: BaseException) -> bool:
    msg = f"{type(exc).__name__}: {exc}".lower()
    needles = (
        "response_format",
        "json_schema",
        "json_object",
        "stream_options",
        "unknown_parameter",
        "unsupported",
        "unrecognized",
        "invalid_request",
        "400",
    )
    return any(n in msg for n in needles)


def _chat_create_with_fallbacks(
    client,
    *,
    model: str,
    messages: list,
    stream: bool,
    create_kwargs: dict[str, Any],
):
    """Try stream_options / json_schema; drop them if the gateway rejects."""
    variants: list[dict[str, Any]] = []
    base = dict(create_kwargs)
    if stream:
        with_so = dict(base)
        with_so["stream_options"] = {"include_usage": True}
        variants.append(with_so)
    variants.append(base)
    expanded: list[dict[str, Any]] = []
    for kw in variants:
        expanded.append(kw)
        rf = kw.get("response_format")
        if isinstance(rf, dict) and rf.get("type") == "json_schema":
            obj_only = dict(kw)
            obj_only["response_format"] = {"type": "json_object"}
            expanded.append(obj_only)
        if "response_format" in kw:
            stripped = dict(kw)
            stripped.pop("response_format", None)
            expanded.append(stripped)
    seen: set[str] = set()
    last: BaseException | None = None
    for kw in expanded:
        sig = str(sorted(kw.keys())) + str(kw.get("response_format"))
        if sig in seen:
            continue
        seen.add(sig)
        try:
            return client.chat.completions.create(
                model=model,
                messages=messages,
                stream=stream,
                **kw,
            )
        except Exception as e:  # noqa: BLE001
            last = e
            if not _unsupported_chat_param(e):
                raise
            continue
    if last is not None:
        raise last
    raise RuntimeError("chat.completions.create failed with no variants")


def call_llm_text(
    *,
    system: str,
    user: str,
    llm_config: str | Path | None = None,
    route: str = "role",
    temperature: float | None = None,
    max_tokens: int | None = None,
    progress: bool = True,
    fallback_stream_on_gateway_timeout: bool = True,
    label: str = "",
    response_format: dict | None = None,
) -> str:
    """Non-streaming chat call (step1 default).

    On gateway timeouts (HTTP 524/504/502 等), optionally falls back to streaming,
    because Cloudflare-style 524 often means the upstream took too long to finish
    a large non-stream response (e.g. full Role library JSON).

    Token usage is recorded into the active ``llm_usage.usage_scope`` when present.
    """
    import sys

    from openai import APIStatusError, APITimeoutError

    from LongDocFmtEval.common.llm_usage import (  # noqa: PLC0415
        estimate_tokens,
        normalize_usage_obj,
        print_usage_summary,
        record_llm_call,
    )

    client, model, create_kwargs = _llm_call_kwargs(
        llm_config=llm_config, route=route, temperature=temperature, max_tokens=max_tokens,
        response_format=response_format,
    )
    if progress:
        sys.stdout.write("    请求中（非流式）…\n")
        sys.stdout.flush()
    try:
        resp = _chat_create_with_fallbacks(
            client,
            model=model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            stream=False,
            create_kwargs=create_kwargs,
        )
    except (APITimeoutError, APIStatusError) as e:
        status = getattr(e, "status_code", None)
        gateway_timeout = isinstance(e, APITimeoutError) or status in (502, 504, 524, 408)
        if not (fallback_stream_on_gateway_timeout and gateway_timeout):
            raise
        print(
            f"[llm] 非流式失败（HTTP {status or 'timeout'}）：多为中转站/上游超时，"
            f"Step1 长 JSON 常见。自动改用流式重试…",
            file=sys.stderr,
        )
        return stream_llm_text(
            system=system,
            user=user,
            llm_config=llm_config,
            route=route,
            temperature=temperature,
            max_tokens=max_tokens,
            progress=progress,
            label=label or "fallback_stream",
            response_format=response_format,
        )

    choice = (resp.choices or [None])[0]
    msg = getattr(choice, "message", None) if choice else None
    text = ""
    if msg is not None:
        text = getattr(msg, "content", None) or ""
        if not text:
            text = getattr(msg, "reasoning_content", None) or ""
    text = str(text or "")
    finish = getattr(choice, "finish_reason", None) if choice else None

    usage = normalize_usage_obj(getattr(resp, "usage", None))
    source = "api"
    if usage["total_tokens"] <= 0:
        usage = {
            "prompt_tokens": estimate_tokens(system) + estimate_tokens(user),
            "completion_tokens": estimate_tokens(text),
            "total_tokens": 0,
        }
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
        source = "estimated"
    rec = record_llm_call(
        model=model,
        prompt_tokens=usage["prompt_tokens"],
        completion_tokens=usage["completion_tokens"],
        total_tokens=usage["total_tokens"],
        source=source,
        stream=False,
        label=label,
        meta={"finish_reason": finish},
    )
    if progress:
        sys.stdout.write(f"    生成完成，共 {len(text)} 字符\n")
        sys.stdout.flush()
        print_usage_summary(
            {
                "calls": 1,
                "prompt_tokens": rec["prompt_tokens"],
                "completion_tokens": rec["completion_tokens"],
                "total_tokens": rec["total_tokens"],
            },
            prefix="    [tokens]",
        )
    if not text.strip():
        print(
            f"[llm] 空响应（非流式）：model={model} finish_reason={finish or '(none)'} "
            f"create_kwargs={ {k: create_kwargs[k] for k in create_kwargs if k != 'extra_body'} }\n"
            f"      输入已送达但输出为空，请检查中转站/模型额度或换模型重试。",
            file=sys.stderr,
        )
    return text


def _is_stream_retryable_error(exc: BaseException) -> bool:
    """True for transient stream/gateway failures worth a short wait + retry."""
    msg = f"{type(exc).__name__}: {exc}".lower()
    needles = (
        "internal_error",
        "stream error",
        "streamreset",
        "rst_stream",
        "connection reset",
        "connection aborted",
        "server disconnected",
        "remoteprotocolerror",
        "read timeout",
        "timed out",
        "temporarily unavailable",
        "502",
        "503",
        "504",
        "524",
    )
    return any(n in msg for n in needles)


def stream_llm_text(
    *,
    system: str,
    user: str,
    llm_config: str | Path | None = None,
    route: str = "role",
    temperature: float | None = None,
    max_tokens: int | None = None,
    progress: bool = True,
    label: str = "",
    retries: int = 2,
    retry_wait: float = 30.0,
    response_format: dict | None = None,
) -> str:
    """Streaming chat call. Returns raw concatenated text.

    Requests ``stream_options.include_usage`` when supported so input/output tokens
    can be recorded (falls back to a char-based estimate if the gateway omits usage).

    On transient stream/gateway errors (e.g. HTTP/2 ``INTERNAL_ERROR``), waits
    ``retry_wait`` seconds and retries up to ``retries`` attempts total.
    """
    import sys
    import time

    last_err: BaseException | None = None
    attempts = max(1, int(retries or 1))
    wait_s = max(0.0, float(retry_wait or 0.0))
    for attempt in range(1, attempts + 1):
        try:
            return _stream_llm_text_once(
                system=system,
                user=user,
                llm_config=llm_config,
                route=route,
                temperature=temperature,
                max_tokens=max_tokens,
                progress=progress,
                label=label,
                response_format=response_format,
            )
        except KeyboardInterrupt:
            raise
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt >= attempts or not _is_stream_retryable_error(e):
                raise
            print(
                f"[llm] 流式中断（第 {attempt}/{attempts} 次）：{e}\n"
                f"      {wait_s:.0f}s 后重试…",
                file=sys.stderr,
            )
            if wait_s > 0:
                time.sleep(wait_s)
    assert last_err is not None
    raise last_err


def _stream_llm_text_once(
    *,
    system: str,
    user: str,
    llm_config: str | Path | None = None,
    route: str = "role",
    temperature: float | None = None,
    max_tokens: int | None = None,
    progress: bool = True,
    label: str = "",
    response_format: dict | None = None,
) -> str:
    import sys

    from LongDocFmtEval.common.llm_usage import (  # noqa: PLC0415
        estimate_tokens,
        normalize_usage_obj,
        print_usage_summary,
        record_llm_call,
    )

    client, model, create_kwargs = _llm_call_kwargs(
        llm_config=llm_config, route=route, temperature=temperature, max_tokens=max_tokens,
        response_format=response_format,
    )
    if progress:
        sys.stdout.write("    请求中（流式，等待首 token）…\n")
        sys.stdout.flush()
    stream = _chat_create_with_fallbacks(
        client,
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        stream=True,
        create_kwargs=create_kwargs,
    )
    chunks: list[str] = []
    total = 0
    last = 0
    finish_reasons: list[str] = []
    usage_raw: Any = None
    first_token = False
    for ev in stream:
        # usage often arrives on a trailing chunk with empty choices
        u = getattr(ev, "usage", None)
        if u is not None:
            usage_raw = u
        if not ev.choices:
            continue
        ch0 = ev.choices[0]
        if getattr(ch0, "finish_reason", None):
            finish_reasons.append(str(ch0.finish_reason))
        delta = ch0.delta
        if not delta:
            continue
        piece = getattr(delta, "content", None) or ""
        if not piece:
            piece = getattr(delta, "reasoning_content", None) or ""
        if piece:
            chunks.append(piece)
            total += len(piece)
            if progress and (not first_token or total - last >= 400):
                first_token = True
                last = total
                sys.stdout.write(f"\r    生成中… {total} 字符")
                sys.stdout.flush()
    text = "".join(chunks)
    usage = normalize_usage_obj(usage_raw)
    source = "api"
    if usage["total_tokens"] <= 0:
        usage = {
            "prompt_tokens": estimate_tokens(system) + estimate_tokens(user),
            "completion_tokens": estimate_tokens(text),
            "total_tokens": 0,
        }
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
        source = "estimated"
    rec = record_llm_call(
        model=model,
        prompt_tokens=usage["prompt_tokens"],
        completion_tokens=usage["completion_tokens"],
        total_tokens=usage["total_tokens"],
        source=source,
        stream=True,
        label=label,
        meta={"finish_reason": ",".join(finish_reasons) if finish_reasons else ""},
    )
    if progress:
        sys.stdout.write(f"\r    生成完成，共 {total} 字符\n")
        sys.stdout.flush()
        print_usage_summary(
            {
                "calls": 1,
                "prompt_tokens": rec["prompt_tokens"],
                "completion_tokens": rec["completion_tokens"],
                "total_tokens": rec["total_tokens"],
            },
            prefix="    [tokens]",
        )
    if not text.strip():
        fr = ",".join(finish_reasons) if finish_reasons else "(none)"
        print(
            f"[llm] 空响应：model={model} finish_reason={fr} "
            f"create_kwargs={ {k: create_kwargs[k] for k in create_kwargs if k != 'extra_body'} }\n"
            f"      输入已送达但输出 0 token，通常是网关/模型侧问题（超时、流式中断、"
            f"或思考链未写入 content），不是 JSON 解析本身的问题。\n"
            f"      建议：在中转站重试；换模型；或把 temperature 降到 0.3～0.7；"
            f"检查账号额度/是否支持长 JSON 输出。",
            file=sys.stderr,
        )
    return text


def call_llm_json(
    *,
    system: str,
    user: str,
    llm_config: str | Path | None = None,
    route: str = "role",
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    cfg = resolve_llm_config_path(llm_config, route=route)
    if not cfg.exists():
        raise FileNotFoundError(
            f"LongDocFmtEval LLM config not found: {cfg}. "
            f"Edit LongDocFmtEval/pipeline_auto/config/llm_config.yaml "
            f"(or role_library_llm_config.yaml / seed_llm_config.yaml)."
        )
    language_model, _mm, llm_params = build_models_from_config(cfg)
    kwargs = dict(llm_params.get("language_model") or {})
    if temperature is not None:
        kwargs["temperature"] = temperature
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    result = language_model.chat_json(system=system, user=user, **kwargs)
    content = result.get("content") if isinstance(result, dict) else result
    if isinstance(content, dict):
        return content
    parsed = safe_json_loads(str(content or ""))
    if not isinstance(parsed, dict):
        raise ValueError(f"LLM did not return a JSON object: {content!r}")
    return parsed
