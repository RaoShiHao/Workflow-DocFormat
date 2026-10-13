"""文生图：主模型 DashScope Qwen-Image；敏感拦截时降级 OpenAI 兼容替补（如 Gemini）。

主路径（qwen-image*）：
    POST {native}/services/aigc/multimodal-generation/generation
    （compatible-mode 不支持；从 dashscope.base_url 推导原生 /api/v1）

替补路径（image_generate_model.fallback，如 gemini-3-pro-image-preview）：
    POST {base}/chat/completions   # OpenAI 兼容；图片在 message.content 的 data URI / markdown 中

配置示例 config/llm_config.yaml：
    image_generate_model:
      provider: dashscope
      model: qwen-image-2.0
      default_params: { size: "1024*1024", ... }
      fallback:
        provider: gemini
        model: gemini-3-pro-image-preview
        default_params: { size: "1024x1024" }
"""
from __future__ import annotations

import base64
import io
import re
from pathlib import Path
from typing import Any

import requests
import yaml

from LongDocFmtEval.common.paths import LLM_CONFIG_PATH

# 常用宽高比 → qwen-image 尺寸（偏小档，控制 PNG 体积；可按需改回 1328*1328 等）
# 总像素需落在模型允许范围（约 512*512～2048*2048）
ASPECT_SIZE = {
    "1:1": "1024*1024",
    "16:9": "1280*720",
    "9:16": "720*1280",
    "4:3": "1024*768",
    "3:4": "768*1024",
}

# qwen size "W*H" → OpenAI-style "WxH"（替补常用档）
_OPENAI_SIZE_BUCKETS = (
    (1024, 1024),
    (1792, 1024),
    (1024, 1792),
)

_MODERATION_MARKERS = (
    "DataInspectionFailed",
    "DataInspection",
    "Green net",
    "green_net",
    "content_filter",
    "content filter",
    "sensitive",
    "敏感",
    "违规",
    "审核",
    "不当内容",
    "unsafe",
    "safety",
    "blocked",
    "moderation",
    "ResponsibleAIPolicyViolation",
)


def _load_config(config_path: str | Path | None = None) -> dict[str, Any]:
    path = Path(config_path) if config_path else LLM_CONFIG_PATH
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _native_base(base_url: str) -> str:
    """把 compatible-mode 的 base_url 转成原生 API base（.../api/v1）。"""
    b = (base_url or "").rstrip("/")
    for suffix in ("/compatible-mode/v1", "/compatible-mode", "/v1"):
        if b.endswith(suffix):
            b = b[: -len(suffix)]
            break
    if b.endswith("/api/v1"):
        return b
    return b + "/api/v1"


def size_for_aspect(aspect: str | None, default: str = "1024*1024") -> str:
    return ASPECT_SIZE.get((aspect or "").strip(), default)


def is_moderation_error(err: str | BaseException) -> bool:
    text = str(err or "")
    return any(m.lower() in text.lower() if m.isascii() else m in text for m in _MODERATION_MARKERS)


def _parse_wh(size: str | None) -> tuple[int, int] | None:
    if not size:
        return None
    m = re.fullmatch(r"\s*(\d+)\s*[x*×]\s*(\d+)\s*", str(size), flags=re.I)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def size_to_openai(size: str | None, default: str = "1024x1024") -> str:
    """Map qwen ``1328*1328`` / openai ``1024x1024`` to a nearby OpenAI size string."""
    wh = _parse_wh(size)
    if not wh:
        return default
    w, h = wh
    best = min(_OPENAI_SIZE_BUCKETS, key=lambda t: abs(t[0] / t[1] - w / h) + abs(t[0] - w) / 4000)
    return f"{best[0]}x{best[1]}"


def _write_image_bytes(out: Path, raw: bytes) -> Path:
    """Persist bytes; convert JPEG→PNG when possible so ``*.png`` assets stay valid."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if raw[:3] == b"\xff\xd8\xff":
        try:
            from PIL import Image  # noqa: PLC0415

            Image.open(io.BytesIO(raw)).convert("RGB").save(out, format="PNG")
            return out
        except Exception:  # noqa: BLE001
            pass
    out.write_bytes(raw)
    return out


def _extract_dashscope_image_url(data: dict[str, Any]) -> str | None:
    for choice in ((data.get("output") or {}).get("choices") or []):
        for item in ((choice.get("message") or {}).get("content") or []):
            if isinstance(item, dict) and item.get("image"):
                return item["image"]
    for res in ((data.get("output") or {}).get("results") or []):
        if isinstance(res, dict) and res.get("url"):
            return res["url"]
    return None


def _extract_openai_compat_image_bytes(data: dict[str, Any]) -> bytes | None:
    """Parse OpenAI chat/images payloads for raw image bytes."""
    for item in data.get("data") or []:
        if not isinstance(item, dict):
            continue
        if item.get("b64_json"):
            return base64.b64decode(item["b64_json"])
        url = item.get("url")
        if isinstance(url, str) and url.startswith("http"):
            r = requests.get(url, timeout=180)
            r.raise_for_status()
            return r.content

    msg = ((data.get("choices") or [{}])[0].get("message") or {})
    content = msg.get("content")

    def from_data_uri(s: str) -> bytes | None:
        m = re.search(r"data:image/[^;]+;base64,([A-Za-z0-9+/=\s]+)", s)
        if not m:
            return None
        return base64.b64decode(re.sub(r"\s+", "", m.group(1)))

    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("b64_json"):
                return base64.b64decode(part["b64_json"])
            iu = part.get("image_url") or {}
            u = iu.get("url") if isinstance(iu, dict) else None
            if isinstance(u, str):
                got = from_data_uri(u)
                if got:
                    return got
                if u.startswith("http"):
                    r = requests.get(u, timeout=180)
                    r.raise_for_status()
                    return r.content
            inline = part.get("inline_data") or part.get("inlineData") or {}
            if isinstance(inline, dict) and inline.get("data"):
                return base64.b64decode(inline["data"])

    if isinstance(content, str):
        got = from_data_uri(content)
        if got:
            return got
        m2 = re.search(r"!\[[^\]]*\]\((https?://[^)]+)\)", content)
        if m2:
            r = requests.get(m2.group(1), timeout=180)
            r.raise_for_status()
            return r.content

    for key in ("images", "image"):
        blob = msg.get(key)
        if isinstance(blob, list) and blob and isinstance(blob[0], dict):
            first = blob[0]
            if first.get("b64_json"):
                return base64.b64decode(first["b64_json"])
            u = first.get("url")
            if isinstance(u, str) and u.startswith("http"):
                r = requests.get(u, timeout=180)
                r.raise_for_status()
                return r.content
    return None


def _generate_dashscope(
    *,
    prompt: str,
    out_path: Path,
    model: str,
    api_key: str,
    base_url: str,
    defaults: dict[str, Any],
    size: str | None,
    negative_prompt: str,
    seed: int | None,
    prompt_extend: bool | None,
    timeout: int,
) -> Path:
    base = _native_base(base_url)
    params: dict[str, Any] = {
        "size": size or defaults.get("size", "1024*1024"),
        "prompt_extend": defaults.get("prompt_extend", True) if prompt_extend is None else prompt_extend,
        "watermark": defaults.get("watermark", False),
    }
    if negative_prompt:
        params["negative_prompt"] = negative_prompt
    if seed is not None:
        params["seed"] = seed

    body = {
        "model": model,
        "input": {"messages": [{"role": "user", "content": [{"text": prompt}]}]},
        "parameters": params,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    url = f"{base}/services/aigc/multimodal-generation/generation"

    resp = requests.post(url, json=body, headers=headers, timeout=timeout)
    if resp.status_code != 200:
        raise RuntimeError(f"文生图请求失败 [{resp.status_code}] {url}\n{resp.text}")
    data = resp.json()
    # DashScope sometimes 200 with error code in body
    code = str(data.get("code") or "")
    msg = str(data.get("message") or "")
    if code and code not in ("200", "Success", "null"):
        raise RuntimeError(f"文生图业务失败 code={code}: {msg or data}")

    img_url = _extract_dashscope_image_url(data)
    if not img_url:
        raise RuntimeError(f"响应未包含图片 URL：{data}")

    img = requests.get(img_url, timeout=timeout)
    img.raise_for_status()
    return _write_image_bytes(out_path, img.content)


_FALLBACK_NO_EXTRA_CAPTION_RULES = (
    "【版式约束·必须遵守 · Layout constraints】\n"
    "1. Output only the main graphic (emblem / photo / diagram / infographic), centered and complete.\n"
    "2. Do NOT add a detached title/caption layer outside the graphic content:\n"
    "   - no figure name / document title / organization name as a big heading under or above the art\n"
    "   - no bilingual title strip, footnote, watermark-like disclaimer, or 'for illustration only' banner\n"
    "   - no poster layout (image on top + explanatory title block below)\n"
    "   - no document-status words painted as labels (e.g. original/copy/draft equivalents in any language)\n"
    "3. In-graphic functional text is allowed when it is part of the depicted object "
    "(flowchart module names, chart axis ticks, UI chrome, street signs, form fields).\n"
    "4. If the subject is an emblem/logo/crest/seal: draw the emblem alone; "
    "do not invent a cover title underneath it.\n"
    "5. 中文同义：只画主体；禁止主体外再叠图名/题注/机构大字/文书名/正本类状态字；"
    "徽标类只画徽标本身；图内功能性文字（流程模块名、坐标轴等）可以保留。"
)

# Clauses that ask the model to paint a title/caption strip (language-agnostic patterns).
_CAPTION_INSTRUCTION_RES: tuple[re.Pattern[str], ...] = (
    # ZH: 下方/上方/底部…配/写…标签/标题/题注…
    re.compile(
        r"[，,]?[^。；\n]{0,12}([上下底]方|四周|周边|左下|右下)[^。；\n]{0,36}"
        r"(配|加|写|附|放|印)?[^。；\n]{0,18}"
        r"(清晰|醒目|粗体)?(的)?"
        r"(中文|英文|双语|中英)?"
        r"(标签|标题|题注|图名|大字|文字条|说明文字)[^。；\n]*",
        re.I,
    ),
    re.compile(
        r"[，,]?[^。；\n]{0,16}(上图下文|海报排版|图下标题|徽标下标题|封面大字标题)[^。；\n]*",
        re.I,
    ),
    re.compile(
        r"[，,]?[^。；\n]{0,20}(含|带有|配有)[^。；\n]{0,12}"
        r"(中文|英文)?(机构名称|文书名称|图题|题注)[^。；\n]*",
        re.I,
    ),
    # EN: below/under the image … title/caption/label
    re.compile(
        r"[,]?[^.?\n]{0,24}(below|under|underneath|beneath|above)\s+(the\s+)?"
        r"(image|graphic|emblem|logo|figure|banner)[^.?\n]{0,48}"
        r"(title|caption|label|heading|text)[^.?\n]*",
        re.I,
    ),
    re.compile(
        r"[,]?[^.?\n]{0,36}(with\s+)?(a\s+|clear\s+|bold\s+)?"
        r"(chinese|english|bilingual)\s+(label|title|caption)s?"
        r"(\s+(below|underneath|under the .{0,20}))?[^.?\n]*",
        re.I,
    ),
    re.compile(
        r"[,]?[^.?\n]{0,28}(poster[- ]style|title\s+block\s+under|caption\s+under)[^.?\n]*",
        re.I,
    ),
)

_SAFE_PREFIX_MARKERS = (
    "【合成数据插图",
    "[Synthetic dataset illustration",
    "（系统说明",
    "(System note",
)


def _strip_caption_instructions(text: str) -> str:
    """Remove generic 'put a title/caption under the graphic' instructions from a prompt body."""
    out = str(text or "")
    for pat in _CAPTION_INSTRUCTION_RES:
        out = pat.sub("", out)
    # Orphan quoted title pairs after 「标签/标题」 cues
    out = re.sub(
        r"(标签|标题|题注|label|title|caption)\s*[为是:=：]?\s*"
        r"[「\"“][^」\"”]{1,40}[」\"”]"
        r"(\s*[与和、,/]\s*[「\"“][^」\"”]{1,40}[」\"”])+",
        "",
        out,
        flags=re.I,
    )
    # Consecutive short quoted phrases often used as painted titles (generic, any language)
    out = re.sub(
        r"([「\"“][^」\"”]{1,24}[」\"”]\s*){2,}",
        "",
        out,
    )
    # Drop leftover 「版式」anti-caption suffix if re-entering
    if "【版式】" in out:
        out = out.split("【版式】", 1)[0]
    if "[Layout]" in out:
        out = out.split("[Layout]", 1)[0]
    out = re.sub(r"[，,]{2,}", "，", out)
    out = re.sub(r"\s{2,}", " ", out)
    out = re.sub(r"[。；]{2,}", "。", out)
    return out.strip(" ，,。.;；")


def _strip_dataset_framing(prompt: str) -> str:
    """Remove dataset/safety prefixes; then scrub caption-strip instructions."""
    text = str(prompt or "").strip()
    if "画面要求：" in text:
        text = text.split("画面要求：", 1)[-1].strip()
    elif "Visual requirements:" in text:
        text = text.split("Visual requirements:", 1)[-1].strip()
    lines = []
    for line in text.splitlines():
        s = line.strip()
        if any(s.startswith(m) for m in _SAFE_PREFIX_MARKERS):
            continue
        lines.append(line)
    text = "\n".join(lines).strip() or text
    text = re.sub(
        r"(图题|题注|标题|图名|caption|title)\s*[为是:=：]\s*[^\n,，；;。.]{1,40}",
        "",
        text,
        flags=re.I,
    )
    text = _strip_caption_instructions(text)
    # Leading planner boilerplate that models often paint as a footer
    text = re.sub(
        r"^(合成数据|版式语义示意|内容虚构|无机密无隐私|synthetic data|layout[- ]?semantic|"
        r"fictional content)([、,/]\s*(合成数据|版式语义示意|内容虚构|无机密无隐私|"
        r"synthetic data|layout[- ]?semantic|fictional content))*[的]?",
        "",
        text,
        count=1,
        flags=re.I,
    )
    # Cover/banner cues strongly induce painted document titles — soften generically
    text = re.sub(
        r"(庄重正式的)?[^，。；]{0,10}(文书|文件|报告|论文|合同)?(首页|封面)横幅",
        "居中抽象徽标或主视觉，周围大幅留白",
        text,
    )
    text = re.sub(
        r"(formal\s+)?(document\s+)?(title[- ]?page|cover)\s*(banner|header|masthead)",
        "centered abstract emblem with wide empty margins",
        text,
        flags=re.I,
    )
    return re.sub(r"\s{2,}", " ", text).strip(" ，,。.;；")


def _with_fallback_no_text_prompt(
    prompt: str,
    *,
    negative_prompt: str = "",
    size_hint: str | None = None,
) -> str:
    """Rewrite prompt for Gemini-like fallback: no extra title/caption strips around the graphic."""
    import uuid  # noqa: PLC0415

    body = _strip_dataset_framing(prompt)
    if not body:
        body = "A clean centered graphic matching the original visual intent, emblem or illustration only."
    # Bust gateway/prompt caches that may return a prior identical raster.
    nonce = uuid.uuid4().hex[:8]
    parts = [
        _FALLBACK_NO_EXTRA_CAPTION_RULES,
        "",
        "【画面语义 / Visual intent】（只描述主体图形；不要把文案画成图下标题）",
        body,
        "",
        "Hard reminder: fill the canvas with the subject graphic only. "
        "Leave surrounding margins empty — no lettering bands under/above the subject.",
        f"(variation={nonce})",
    ]
    avoid = [
        "detached title under graphic",
        "caption strip",
        "poster layout with text below image",
        "organization-name heading under emblem",
        "document-title heading under emblem",
        "bilingual title block",
        "disclaimer footer",
        "图名题注条",
        "上图下文",
        "机构大字标题",
        "文书名标题",
        "any readable heading under the main emblem",
    ]
    if negative_prompt.strip():
        avoid.insert(0, _strip_caption_instructions(negative_prompt.strip()) or negative_prompt.strip())
    parts.extend(["", "【Negative / avoid】", ", ".join(avoid)])
    if size_hint:
        parts.extend(["", f"(Canvas about {size_hint}.)"])
    return "\n".join(parts)


def _generate_openai_compat_chat(
    *,
    prompt: str,
    out_path: Path,
    model: str,
    api_key: str,
    base_url: str,
    defaults: dict[str, Any],
    size: str | None,
    timeout: int,
    negative_prompt: str = "",
) -> Path:
    """OpenAI-compatible chat completions that return an inline image (Gemini 中转等)."""
    base = (base_url or "").rstrip("/")
    oai_size = size_to_openai(size or defaults.get("size"), defaults.get("size", "1024x1024"))
    # Gemini often paints a title/caption strip under the graphic; wrap unless disabled.
    if defaults.get("no_text_framing", True):
        user_text = _with_fallback_no_text_prompt(
            prompt, negative_prompt=negative_prompt, size_hint=oai_size,
        )
        # One-line visibility: confirm rewrite actually ran (not silent cache of old prompt).
        print(f"  [fallback-prompt] chars={len(user_text)} head={user_text[:160].replace(chr(10), ' / ')}…")
    else:
        user_text = prompt
        if oai_size and "size" not in prompt.lower():
            user_text = f"{prompt}\n\n(Preferred output size approximately {oai_size}.)"

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    url = f"{base}/chat/completions"
    body = {
        "model": model,
        "messages": [{"role": "user", "content": user_text}],
    }
    resp = requests.post(url, json=body, headers=headers, timeout=timeout)
    if resp.status_code != 200:
        raise RuntimeError(f"替补文生图失败 [{resp.status_code}] {url}\n{resp.text}")
    data = resp.json()
    raw = _extract_openai_compat_image_bytes(data)
    if not raw:
        raise RuntimeError(f"替补响应未包含图片：{str(data)[:800]}")
    return _write_image_bytes(out_path, raw)


def _run_provider(
    *,
    provider: str,
    model: str,
    api_key: str,
    base_url: str,
    defaults: dict[str, Any],
    prompt: str,
    out_path: Path,
    size: str | None,
    negative_prompt: str,
    seed: int | None,
    prompt_extend: bool | None,
    timeout: int,
) -> Path:
    p = (provider or "").strip().lower()
    if p in {"dashscope", "qwen"}:
        return _generate_dashscope(
            prompt=prompt,
            out_path=out_path,
            model=model,
            api_key=api_key,
            base_url=base_url,
            defaults=defaults,
            size=size,
            negative_prompt=negative_prompt,
            seed=seed,
            prompt_extend=prompt_extend,
            timeout=timeout,
        )
    if p in {"gemini", "openai", "openai_compat", "openai-compatible"}:
        return _generate_openai_compat_chat(
            prompt=prompt,
            out_path=out_path,
            model=model,
            api_key=api_key,
            base_url=base_url,
            defaults=defaults,
            size=size,
            timeout=timeout,
            negative_prompt=negative_prompt,
        )
    raise RuntimeError(f"未知文生图 provider={provider!r}（支持 dashscope / gemini / openai）")


def generate_image(
    prompt: str,
    out_path: str | Path,
    *,
    size: str | None = None,
    negative_prompt: str = "",
    seed: int | None = None,
    prompt_extend: bool | None = None,
    timeout: int = 300,
    allow_fallback: bool = True,
    config_path: str | Path | None = None,
) -> Path:
    """按 prompt 生成一张图并保存到 out_path。主模型失败且判定为敏感审核时走 fallback。

    ``config_path``: 可选，指向含 ``image_generate_model`` 的 yaml（如 pipeline_auto
    的 ``llm_config.yaml``）。默认仍用 ``common.paths.LLM_CONFIG_PATH``（pipeline_role）。
    """
    cfg = _load_config(config_path)
    spec = cfg.get("image_generate_model") or {}
    provider = spec.get("provider", "dashscope")
    model = spec.get("model")
    defaults = dict(spec.get("default_params") or {})

    prov = cfg.get(provider) or {}
    api_key = prov.get("api_key")
    base_url = prov.get("base_url") or ""
    if not model or not api_key:
        raise RuntimeError(
            f"image_generate_model 未配置好：model={model!r}，provider={provider!r} 缺 api_key"
        )

    out = Path(out_path)
    try:
        return _run_provider(
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=base_url,
            defaults=defaults,
            prompt=prompt,
            out_path=out,
            size=size,
            negative_prompt=negative_prompt,
            seed=seed,
            prompt_extend=prompt_extend,
            timeout=timeout,
        )
    except Exception as primary_exc:  # noqa: BLE001
        fb = spec.get("fallback") or {}
        if not (allow_fallback and isinstance(fb, dict) and fb.get("model") and is_moderation_error(primary_exc)):
            raise

        fb_provider = fb.get("provider") or "gemini"
        fb_model = fb.get("model")
        fb_defaults = dict(fb.get("default_params") or {})
        fb_prov = cfg.get(fb_provider) or {}
        fb_key = fb_prov.get("api_key")
        fb_base = fb_prov.get("base_url") or ""
        if not fb_key:
            raise RuntimeError(
                f"主模型敏感拦截且 fallback 缺 api_key（provider={fb_provider!r}）"
            ) from primary_exc

        print(
            f"  [fallback] 主模型敏感拦截 → {fb_provider}/{fb_model} "
            f"（{str(primary_exc).splitlines()[-1][:120]}）"
        )
        return _run_provider(
            provider=str(fb_provider),
            model=str(fb_model),
            api_key=fb_key,
            base_url=fb_base,
            defaults=fb_defaults,
            prompt=prompt,
            out_path=out,
            size=size or fb_defaults.get("size"),
            negative_prompt=negative_prompt,
            seed=seed,
            prompt_extend=False,
            timeout=timeout,
        )


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="文生图自测（主模型 + 可选敏感降级）")
    ap.add_argument("--prompt", default="社区早餐摊张贴扫码支付牌与团购码的现场照片，写实风格，自然光")
    ap.add_argument("--out", default="LongDocFmtEval/pipeline_role/work/_imgtest/sample.png")
    ap.add_argument("--aspect", default="4:3", help="1:1 / 16:9 / 9:16 / 4:3 / 3:4")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--no-fallback", action="store_true")
    args = ap.parse_args()

    path = generate_image(
        args.prompt,
        args.out,
        size=size_for_aspect(args.aspect),
        seed=args.seed,
        allow_fallback=not args.no_fallback,
    )
    print(f"[image] saved -> {path.resolve()}")
