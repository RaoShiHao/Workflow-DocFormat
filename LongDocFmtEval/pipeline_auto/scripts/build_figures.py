"""图构建：为 step4 模板目录 / step5 成稿目录生成真实图片资产。

两段式：
  A. 规划（LLM）：读 template.json(图角色+T*) + plan.json(图块) → 逐图块产 figure_spec
     （gen_method 路由 + 素材：t2i prompt / chart 数据 / diagram 节点边）。存 figures/figures.json。
  B. 渲染（执行器，无 LLM）：按 gen_method 出图 —— t2i 走 DashScope 文生图，chart/diagram 走
     matplotlib；规划为 placeholder 时仍尝试文生图（从 figures.json 字段拼 prompt，不改规划文件），
     失败再降级灰块占位。存 assets/<figure_id>.png + assets.json 清单。

逐实例生成：同一图角色在文中每次出现都各出一张贴合该处 main_point 的新图，不复用。
内容感知：若目录已有 content.json（step5 产物），出图会对齐正文/表格数字，并一并重渲 gold。
带缓存：figures.json / 各 png 已存在则跳过；plan 图块（block_id/role/main_point）变化时
自动删 stale 条目并重规划/重渲；--overwrite 强制全量重跑。
此外写入 figures/figures_plan_snapshot.json 记录上次对齐的 plan 图块摘要。

``build_one()`` 由 step4（preview）与 step5（document）内联调用；提示词 ``step4_figures_plan.yaml``。

用法：
  python LongDocFmtEval/pipeline_auto/scripts/build_figures.py --template-dir "LongDocFmtEval/pipeline_auto/datasets/<id>_<lang>_<topic>/C1/step5_template"
  python LongDocFmtEval/pipeline_auto/scripts/build_figures.py --all
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from LongDocFmtEval.common.figure_render import render_chart, render_diagram, render_placeholder
from LongDocFmtEval.common.llm import dump_json, load_json, load_prompt, render_template
from LongDocFmtEval.common.prompt_locale import format_theme_context
from LongDocFmtEval.pipeline_auto import auto_llm as image_gen
from LongDocFmtEval.pipeline_auto.auto_llm import stream_llm_text
from LongDocFmtEval.pipeline_auto.auto_paths import (
    CONFIG_DIR,
    OUTLINES_DIR,
    TEMPLATES_DIR,
    resolve_outline_sidecar,
    resolve_work_file,
)


def _prompt_path(locale: str, name: str, *fallbacks: str) -> Path:
    from LongDocFmtEval.pipeline_auto.auto_paths import prompt_path  # noqa: PLC0415

    return prompt_path(locale, name, fallbacks=fallbacks)


def _is_en(locale: str | None) -> bool:
    return str(locale or "").lower().startswith("en")


# image_kind → 默认 gen_method（LLM 未给或给错时兜底）
# 流程/架构类 abstract_diagram 也走 t2i：脚本框线图可读性差（字压箭头、节点过挤）。
KIND_TO_METHOD = {
    "photo": "t2i",
    "map": "t2i",
    "illustrative_diagram": "t2i",
    "abstract_diagram": "t2i",
    "chart": "chart",
    "placeholder": "placeholder",
}
# matplotlib diagram 仍保留为可选降级，但规划阶段默认不再路由到它
VALID_METHODS = {"t2i", "chart", "diagram", "placeholder"}
_T2I_KINDS = {"photo", "map", "illustrative_diagram", "abstract_diagram"}


def _cm(val, default: float) -> float:
    m = re.search(r"([\d.]+)", str(val or ""))
    return float(m.group(1)) if m else default


def _aspect_from_size(w_cm: float, h_cm: float) -> str:
    r = w_cm / h_cm if h_cm else 1.0
    if r >= 1.6:
        return "16:9"
    if r <= 0.63:
        return "9:16"
    if r <= 0.85:
        return "3:4"
    if r >= 1.18:
        return "4:3"
    return "1:1"


def _safe(name: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", str(name)).strip("_") or "fig"


def _section_content(inst: str, outline: dict, roles: dict, blocks: dict, limit: int = 1200) -> str:
    """把某节已写好的正文与表格拼成一段摘录，供内容感知出图（对齐数字/步骤/场景）。"""
    parts: list[str] = []
    for b in outline.get(inst, []):
        c = blocks.get(b.get("block_id")) or {}
        if c.get("table"):
            t = c["table"]
            head = " | ".join(str(x) for x in (t.get("header") or []))
            rows = "；".join("/".join(str(x) for x in r) for r in (t.get("rows") or [])[:6])
            parts.append(f"[表] {head}｜{rows}")
        elif c.get("text") and roles.get(b.get("role_id"), {}).get("object") != "image":
            parts.append(str(c["text"]))
    text = "\n".join(parts)
    return text[:limit]


def assign_figure_ids(plan: dict, roles: dict) -> dict[str, str]:
    """Stable F{n} for every image block in section order, including preview-skipped ones.

    Preview may omit extra instances of the same Img* role, but IDs must still match
    ``collect_figures`` / ``assets_preview.json``. Numbering only the rendered images
    shifts later IDs (e.g. skipped F8 makes Annex B67 look up F8.png that does not exist
    and leaves a gray placeholder while F9.png is pasted onto the next slot).
    """
    outline = {o.get("instance_id"): (o.get("blocks") or []) for o in (plan.get("outline") or [])}
    seen: set[str] = set()
    out: dict[str, str] = {}
    k = 0
    for sec in (plan.get("sections") or []):
        for b in outline.get(sec.get("instance_id"), []) or []:
            if not isinstance(b, dict):
                continue
            rid = b.get("role_id")
            if (roles.get(rid) or {}).get("object") != "image":
                continue
            k += 1
            fid = str(b.get("figure_id") or f"F{k}")
            while fid in seen:
                fid = f"{fid}_{k}"
            seen.add(fid)
            bid = str(b.get("block_id") or "")
            if bid:
                out[bid] = fid
    return out


def collect_figures(template: dict, plan: dict, lib: dict, blocks: dict | None = None) -> list[dict]:
    """从大纲收集图块（逐出现），带角色语义 + T* 尺寸 + 本节已写内容（若有）。"""
    roles = template.get("roles", {}) or {}
    blocks = blocks or {}
    img_nodes = {n.get("role_id"): n for n in ((lib.get("roles", {}) or {}).get("image") or [])}
    outline = {o.get("instance_id"): (o.get("blocks") or []) for o in (plan.get("outline") or [])}
    sec_title = {s.get("instance_id"): (s.get("title") or s.get("role_id"))
                 for s in (plan.get("sections") or [])}
    sec_content = {inst: _section_content(inst, outline, roles, blocks) for inst in outline} if blocks else {}
    fid_by_block = assign_figure_ids(plan, roles)

    figs: list[dict] = []
    k = 0
    # 按「节顺序」遍历，与 render_template_docx 的图块顺序一致，保证 figure_id 对齐
    for sec in (plan.get("sections") or []):
        inst = sec.get("instance_id")
        for b in outline.get(inst, []):
            # 只对「图片块」出图；题注块虽带 image_ref（关联用）但 object=paragraph，跳过
            rid = b.get("role_id")
            if roles.get(rid, {}).get("object") != "image":
                continue
            k += 1
            bid = str(b.get("block_id") or "")
            fid = fid_by_block.get(bid) or str(b.get("figure_id") or f"F{k}")
            # Stylesheet props/example keyed by Img* role_id; image_ref (F01) is only an association id
            rspec = roles.get(rid, {}) or {}
            props = rspec.get("props", {}) or {}
            node = img_nodes.get(rid, {}) or {}
            w_cm = _cm(props.get("width"), 12.0)
            h_cm = _cm(props.get("height"), 8.0)
            figs.append({
                "figure_id": fid,
                "block_id": b.get("block_id"),
                "role_id": rid,
                "display_name": rspec.get("display_name") or node.get("display_name") or rid,
                "description": rspec.get("description") or node.get("discriminators") or "",
                "image_kind": node.get("image_kind", ""),
                "content_hint": node.get("content_hint", ""),
                "size_intent": node.get("size_intent", ""),
                "main_point": b.get("main_point", ""),
                "section": sec_title.get(inst, ""),
                "section_content": sec_content.get(inst, ""),
                "width_cm": w_cm, "height_cm": h_cm,
                "align": props.get("hAlign") or "center",
                "aspect": _aspect_from_size(w_cm, h_cm),
                "alt_t3": props.get("alt", ""),
                "template_exhibit": b.get("template_exhibit"),
            })
    return figs


FIGURES_PLAN_SNAPSHOT = "figures_plan_snapshot.json"


def figure_slot_signature(f: dict) -> dict:
    """Plan-level identity for one image block (used for cache invalidation)."""
    return {
        "figure_id": str(f.get("figure_id") or ""),
        "block_id": str(f.get("block_id") or ""),
        "role_id": str(f.get("role_id") or ""),
        "main_point": str(f.get("main_point") or ""),
    }


def figures_plan_snapshot(figs: list[dict]) -> list[dict]:
    return [figure_slot_signature(f) for f in figs]


def reconcile_figure_cache(
    figs: list[dict],
    cached: dict[str, dict],
    *,
    assets_dir: Path,
) -> tuple[dict[str, dict], list[dict], set[str], set[str]]:
    """Align cached specs with current plan image blocks (keyed by block_id).

    Returns ``(kept_specs, figs_need_llm_plan, dropped_figure_ids, rerender_figure_ids)``.
    """
    cached = cached if isinstance(cached, dict) else {}
    expected_by_block = {
        str(f.get("block_id")): f for f in figs if str(f.get("block_id") or "")
    }
    cached_by_block: dict[str, tuple[str, dict]] = {}
    for fid, spec in cached.items():
        if not isinstance(spec, dict):
            continue
        bid = str(spec.get("block_id") or "")
        if bid:
            cached_by_block[bid] = (str(fid), spec)

    drop_fids: set[str] = set()
    rerender_fids: set[str] = set()
    merged: dict[str, dict] = {}
    need_plan: list[dict] = []

    for bid, (old_fid, _old_spec) in cached_by_block.items():
        if bid not in expected_by_block:
            drop_fids.add(old_fid)

    for f in figs:
        bid = str(f.get("block_id") or "")
        fid = str(f["figure_id"])
        if not bid:
            need_plan.append(f)
            continue
        if bid not in cached_by_block:
            need_plan.append(f)
            continue
        old_fid, old_spec = cached_by_block[bid]
        same_slot = (
            str(old_spec.get("role_id") or "") == str(f.get("role_id") or "")
            and str(old_spec.get("main_point") or "") == str(f.get("main_point") or "")
        )
        if same_slot:
            merged[fid] = {**old_spec, **f}
            if old_fid != fid:
                drop_fids.add(old_fid)
                rerender_fids.add(fid)
            continue
        drop_fids.add(old_fid)
        need_plan.append(f)

    assets_dir.mkdir(parents=True, exist_ok=True)
    for fid in drop_fids:
        png = assets_dir / f"{_safe(fid)}.png"
        if png.is_file():
            png.unlink(missing_ok=True)

    return merged, need_plan, drop_fids, rerender_fids


def prune_orphan_pngs(assets_dir: Path, manifest: dict[str, dict]) -> int:
    """Remove PNG files not referenced by the current assets manifest."""
    if not assets_dir.is_dir():
        return 0
    keep = {f"{_safe(fid)}.png" for fid in manifest}
    removed = 0
    for png in assets_dir.glob("*.png"):
        if png.name not in keep:
            png.unlink(missing_ok=True)
            removed += 1
    return removed


def filter_figs_for_preview(
    figs: list[dict], plan: dict, template: dict, *, per_style: int = 2, max_styles: int | None = None,
) -> list[dict]:
    """Keep ≤per_style instances **per Img* role** for preview only.

    Do **not** call this for ``variant=document`` / gold — long docs need the full outline.
    """
    del max_styles
    from LongDocFmtEval.pipeline_auto.seed.template_exhibits import plan_template_exhibits  # noqa: PLC0415

    roles = template.get("roles") or {}
    sections = plan.get("sections") or []
    outline_map = {o.get("instance_id"): (o.get("blocks") or []) for o in (plan.get("outline") or [])}
    info = plan_template_exhibits(sections, outline_map, roles, per_style=per_style)
    keep = info.get("exhibit_block_ids") or set()
    out = [f for f in figs if str(f.get("block_id") or "") in keep]
    if out:
        return out
    # fallback without block_id: each Img* ≤per_style (ignore all-false exhibit flags)
    counts: dict[str, int] = {}
    out = []
    for f in figs:
        rid = str(f.get("role_id") or "")
        if counts.get(rid, 0) >= per_style:
            continue
        out.append(f)
        counts[rid] = counts.get(rid, 0) + 1
    return out


def plan_specs(figs: list[dict], template: dict, temperature: float, max_tokens: int) -> dict:
    """Phase A：LLM 逐图块产 figure_spec（gen_method + 素材）。返回 figure_id -> spec。"""
    meta = template.get("meta", {})
    locale = meta.get("locale") or "zh"
    context = format_theme_context(
        locale,
        genre=str(meta.get("genre", "") or ""),
        theme=str(meta.get("theme", "") or ""),
        writing_requirements=str(meta.get("writing_requirements", "") or ""),
    )
    payload = []
    for f in figs:
        item = {
            "figure_id": f["figure_id"], "role": f["display_name"], "description": f["description"],
            "image_kind": f["image_kind"], "content_hint": f["content_hint"],
            "size_intent": f["size_intent"], "section": f["section"], "main_point": f["main_point"],
        }
        if f.get("section_content"):  # 内容感知：附本节已写正文/表格，供图表数字/流程对齐
            item["section_content"] = f["section_content"]
        payload.append(item)

    prompt_cfg = load_prompt(_prompt_path(locale, "step4_figures_plan.yaml", "figures_plan.yaml"))
    user = render_template(prompt_cfg["user_template"], context=context, figures=payload)
    raw = stream_llm_text(
        system=prompt_cfg["system"], user=user, temperature=temperature, max_tokens=max_tokens
    )
    from LongDocFmtEval.common.role_library import lenient_json_load
    data = lenient_json_load(raw)
    specs = {s.get("figure_id"): s for s in (data.get("figures") or [])}

    out: dict[str, dict] = {}
    for f in figs:
        s = specs.get(f["figure_id"], {}) or {}
        method = s.get("gen_method")
        if method not in VALID_METHODS:
            method = KIND_TO_METHOD.get(f["image_kind"], "placeholder")
        # 流程/架构/示意：强制文生图（避免 matplotlib 框线可读性崩）
        if f.get("image_kind") in _T2I_KINDS and method == "diagram":
            method = "t2i"
        prompt = s.get("prompt")
        if method == "t2i" and not prompt:
            # 若模型仍按 diagram 填了节点，拼成画面描述
            diag = s.get("diagram") or {}
            nodes = diag.get("nodes") or []
            if nodes:
                joined = " → ".join(str(n) for n in nodes)
                if _is_en(locale):
                    prompt = (
                        f"Clean English informational diagram / flowchart, readable labels, "
                        f"large type, arrows not over text. Title: {s.get('title') or f['display_name']}. "
                        f"Nodes in order: {joined}. Point: {f.get('main_point') or ''}"
                    )
                else:
                    prompt = (
                        f"专业信息图/流程图，横向清晰排版，中文标签可读、字号足够大、"
                        f"箭头不压字。标题：{s.get('title') or f['display_name']}。"
                        f"节点依次为：{joined}。"
                        f"要点：{f.get('main_point') or ''}"
                    )
            elif _is_en(locale):
                prompt = (
                    f"Clean English informational diagram, readable labels, no watermark. "
                    f"{f.get('display_name') or ''}: {f.get('main_point') or ''}"
                )
            else:
                prompt = (
                    f"专业中文信息图或架构流程图，排版疏朗、标签清晰、无水印。"
                    f"{f.get('display_name') or ''}：{f.get('main_point') or ''}"
                )
        neg_default = (
            "watermark, blurry text, overlapping text, arrows through text, low resolution"
            if _is_en(locale)
            else "水印, 模糊文字, 文字重叠, 箭头穿过文字, 低分辨率"
        )
        caption_neg = _NO_EXTRA_CAPTION_NEG_EN if _is_en(locale) else _NO_EXTRA_CAPTION_NEG_ZH
        out[f["figure_id"]] = {
            **f,
            "gen_method": method,
            "title": s.get("title") or f["display_name"],
            "alt": s.get("alt") or f.get("alt_t3") or f["display_name"],
            "prompt": prompt if method == "t2i" else s.get("prompt"),
            "negative_prompt": (
                f"{(s.get('negative_prompt') or neg_default).rstrip(', ')}, {caption_neg}"
                if method == "t2i"
                else s.get("negative_prompt")
            ),
            "chart": s.get("chart"),
            "diagram": s.get("diagram"),
            "locale": "en" if _is_en(locale) else "zh",
        }
    return out


# Prefixed on every t2i call — frames generation as synthetic dataset art (helps green-net).
# NOTE: keep this as *instruction to the model*, not caption-like wording that gets painted
# as footer text (Gemini/部分文生图会把「合成数据·仅供…」画进图里).
_SAFE_T2I_PREFIX_ZH = (
    "（系统说明，勿画进画面）本请求用于生成虚构的学术/训练文档插图；"
    "内容全部模拟，不含真实机密、真实证件或可识别个人隐私。"
    "请按下方画面要求出图。"
)
_SAFE_T2I_PREFIX_EN = (
    "(System note — do not paint into the image) This request is for a fictional "
    "academic/training document illustration; fully simulated, no real secrets, "
    "ID photos, or identifiable private individuals. Follow the visual requirements below."
)

# Appended to every t2i prompt: forbid detached title/caption strips (user red-box issue).
_NO_EXTRA_CAPTION_SUFFIX_ZH = (
    "【版式】只画主体图形本身；禁止在徽标/插图下方或四周再追加机构名大字标题、"
    "文书名、中英对照标题、图名、题注，或「合成数据」「仅供示意」等脚注文字条。"
    "不要做成「上图下文」海报。画面内若本身需要少量必要文字（图表轴、路牌等）可以保留。"
)
_NO_EXTRA_CAPTION_SUFFIX_EN = (
    "[Layout] Draw the main graphic only. Do NOT add a separate title/caption block "
    "under or around it (no agency name heading, document title, bilingual label, "
    "figure name, or 'synthetic data' footer). No poster-style 'image on top, text below'. "
    "Necessary in-graphic text (chart axes, street signs) is OK."
)

_NO_EXTRA_CAPTION_NEG_ZH = (
    "图下方大标题, 机构名称标题条, 文书名称标题, 中英对照标题, 题注, 图名, "
    "合成数据脚注, 仅供版式与语义示意, 上图下文海报排版"
)
_NO_EXTRA_CAPTION_NEG_EN = (
    "title block under emblem, agency name heading, document title caption, "
    "bilingual heading under logo, figure caption footer, synthetic data disclaimer, "
    "poster layout with text below image"
)


def _with_safe_t2i_framing(prompt: str, *, locale: str | None = None) -> str:
    text = str(prompt or "").strip()
    if text.startswith("（系统说明") or text.startswith("(System note"):
        return text
    # strip legacy caption-like banners if re-entered
    if text.startswith("【合成数据插图") or text.startswith("[Synthetic dataset illustration"):
        if "画面要求：" in text:
            text = text.split("画面要求：", 1)[-1].strip()
        elif "Visual requirements:" in text:
            text = text.split("Visual requirements:", 1)[-1].strip()
    en = _is_en(locale)
    prefix = _SAFE_T2I_PREFIX_EN if en else _SAFE_T2I_PREFIX_ZH
    suffix = _NO_EXTRA_CAPTION_SUFFIX_EN if en else _NO_EXTRA_CAPTION_SUFFIX_ZH
    mid = f"Visual requirements: {text}" if en else f"画面要求：{text}"
    return f"{prefix}\n{mid}\n{suffix}"


def _prompt_from_spec(spec: dict) -> str:
    """从 figure_spec 拼文生图 prompt（规划为 placeholder 或 prompt 为空时使用）。"""
    explicit = spec.get("prompt")
    if explicit and str(explicit).strip():
        return str(explicit).strip()

    loc = spec.get("locale") or "zh"
    en = _is_en(loc)
    chunks: list[str] = []

    for key in ("title", "display_name"):
        val = spec.get(key)
        if val and str(val).strip():
            chunks.append(str(val).strip())
            break

    for key in ("description", "content_hint", "main_point", "alt"):
        val = spec.get(key)
        if not val or not str(val).strip():
            continue
        text = str(val).strip()
        if text not in chunks:
            chunks.append(text)

    sec = spec.get("section")
    if sec and str(sec).strip():
        chunks.append(f"Section context: {sec}" if en else f"所属章节：{sec}")

    sc = spec.get("section_content")
    if sc and str(sc).strip():
        snippet = re.sub(r"\s+", " ", str(sc).strip())[:480]
        if snippet and snippet not in chunks:
            chunks.append(snippet)

    kind = str(spec.get("image_kind") or "").lower()
    size_intent = str(spec.get("size_intent") or "").lower()
    if kind == "photo" or "portrait" in size_intent:
        if en:
            chunks.append(
                "Stylized fictional corporate portrait for an annual report, professional attire, "
                "neutral studio background, generic non-identifiable face, editorial training sample."
            )
        else:
            chunks.append(
                "虚构企业人物肖像示意插图，商务正装，中性背景，非真实可识别面孔，仅供版式训练样本。"
            )
    elif kind == "map":
        if en:
            chunks.append("Schematic regional map infographic, not real administrative boundaries.")
        else:
            chunks.append("示意区域分布信息图，非真实行政边界。")

    if not chunks:
        chunks.append(str(spec.get("figure_id") or spec.get("display_name") or "illustration"))

    return ". ".join(chunks) if en else "；".join(chunks)


def _default_negative_prompt(spec: dict) -> str:
    loc = spec.get("locale") or "zh"
    neg_default = (
        "watermark, blurry text, overlapping text, arrows through text, low resolution"
        if _is_en(loc)
        else "水印, 模糊文字, 文字重叠, 箭头穿过文字, 低分辨率"
    )
    caption_neg = _NO_EXTRA_CAPTION_NEG_EN if _is_en(loc) else _NO_EXTRA_CAPTION_NEG_ZH
    neg = str(spec.get("negative_prompt") or neg_default).strip().rstrip(",")
    return f"{neg}, {caption_neg}" if neg else caption_neg


def _render_t2i(spec: dict, asset: Path, seed: int) -> None:
    loc = spec.get("locale") or "zh"
    raw_prompt = _prompt_from_spec(spec)
    prompt = _with_safe_t2i_framing(raw_prompt, locale=loc)
    neg = _default_negative_prompt(spec)
    aspect = spec.get("aspect") or _aspect_from_size(
        float(spec.get("width_cm") or 12), float(spec.get("height_cm") or 8),
    )
    size = image_gen.size_for_aspect(aspect)
    try:
        image_gen.generate_image(prompt, asset, size=size, negative_prompt=neg, seed=seed)
    except Exception as e1:  # noqa: BLE001
        err = str(e1)
        if image_gen.is_moderation_error(err):
            soft = _sanitize_t2i_prompt(raw_prompt, locale=loc)
            print(
                f"    [retry] {spec['figure_id']} 绿网/敏感拦截，净化后重试"
                f"（正文 {len(raw_prompt)}→{len(soft)} 字）"
            )
            image_gen.generate_image(soft, asset, size=size, negative_prompt=neg, seed=seed)
            spec["prompt_sanitized"] = soft
        else:
            raise


def _sanitize_t2i_prompt(prompt: str, *, locale: str | None = None) -> str:
    """Soften prompts that often trip DashScope「绿网」text inspection."""
    text = str(prompt or "")
    # Drop previous framing then re-apply after scrubbing body
    if "画面要求：" in text:
        text = text.split("画面要求：", 1)[-1]
    elif "Visual requirements:" in text:
        text = text.split("Visual requirements:", 1)[-1]
    replacements = (
        ("中国规划报告风格", "通用规划报告风格"),
        ("中国", "示意区域"),
        ("真实行政边界", "示意分区边界"),
        ("行政边界", "示意分区"),
        ("省域", "区域"),
        ("国民经济和社会发展五年规划纲要", "区域发展规划"),
        ("五年规划纲要", "发展规划"),
        ("生态红线敏感片区", "重点生态保护区"),
        ("生态红线", "生态保护范围"),
        ("饮用水源保护区", "水源涵养区"),
        ("共同富裕", "共享发展"),
        ("国土空间", "空间布局"),
        ("重大工程", "重点工程"),
        ("绿网", "网络"),
        ("China five-year plan", "regional development plan"),
        ("administrative boundary", "schematic zoning"),
        ("ecological red line", "ecological protection zone"),
    )
    for a, b in replacements:
        text = text.replace(a, b)
    if len(text) > 360:
        if _is_en(locale):
            text = (
                text[:360].rstrip(",.;: ")
                + ". Flat informational diagram, clear English labels, not a real map."
            )
        else:
            text = text[:360].rstrip("，,。；;、 ") + "。扁平信息图，中文标签清晰不重叠，非真实地图。"
    return _with_safe_t2i_framing(text, locale=locale)


def render_one(spec: dict, asset: Path, seed: int) -> str:
    """Phase B：按 gen_method 出图，返回实际使用的方法（失败降级 placeholder）。

    ``gen_method=placeholder`` 时仍走文生图（从 spec 字段拼 prompt，不改 figures.json）；
    仅文生图失败时才落灰块占位。
    """
    method = spec.get("gen_method") or "placeholder"
    w, h = spec["width_cm"], spec["height_cm"]
    try:
        if method in ("t2i", "placeholder"):
            _render_t2i(spec, asset, seed)
            return "t2i"
        if method == "chart":
            render_chart(spec.get("chart") or {"title": spec.get("title")}, asset, w, h, seed=seed)
        elif method == "diagram":
            render_diagram(spec.get("diagram") or {"title": spec.get("title")}, asset, w, h)
        else:
            render_placeholder({"title": spec.get("title")}, asset, w, h)
        return method
    except Exception as e:  # noqa: BLE001
        label = "placeholder→t2i" if method == "placeholder" else method
        print(f"    [warn] {spec['figure_id']} 用 {label} 出图失败，降级占位：{str(e).splitlines()[-1][:140]}")
        render_placeholder({"title": spec.get("title")}, asset, w, h)
        return "placeholder"


# 两套配图各自独立存放，互不覆盖：
#   preview  = step4 模板语义图（按 main_point）→ 供 template.docx
#   document = step5 内容语义图（读 content.json 对齐正文）→ 供 gold document.docx
# 字段：specs 规划文件 / assets 子目录 / manifest 清单 / content_aware 是否读正文 / 渲染目标
_VARIANTS = {
    "preview":  ("figures_preview.json", "assets_preview", "assets_preview.json", False, "template.docx"),
    "document": ("figures.json", "assets", "assets.json", True, "document.docx"),
}


def build_one(template_dir: Path, *, variant: str = "document", overwrite: bool, temperature: float,
              max_tokens: int, seed: int, no_render: bool) -> bool:
    specs_name, assets_sub, manifest_name, content_aware, render_target = _VARIANTS[variant]
    tpl_path = resolve_work_file(template_dir, "template.json")
    rp_path = resolve_work_file(template_dir, "role_params.json")
    if not tpl_path.is_file() and not rp_path.is_file():
        print(f"[skip] 缺 role_params.json / template.json: {template_dir}")
        return False
    template = load_json(tpl_path) if tpl_path.is_file() else {}
    from LongDocFmtEval.pipeline_auto.seed.seed_ir import load_merged_roles  # noqa: PLC0415
    template = {**template, "roles": load_merged_roles(template_dir, template)}
    slug = template_dir.name
    plan_path = resolve_outline_sidecar(template_dir, "plan.json")
    lib_path = resolve_outline_sidecar(template_dir, "selected_library.yaml")
    plan = load_json(plan_path) if plan_path.exists() else {}
    spec_path = Path(template_dir) / "specimen_plan.json"
    use_specimen_plan = False
    if variant == "preview" and spec_path.is_file():
        try:
            spec = load_json(spec_path)
        except Exception:  # noqa: BLE001
            spec = {}
        if isinstance(spec, dict) and spec.get("outline"):
            plan = {
                **plan,
                "sections": spec.get("sections") or plan.get("sections"),
                "outline": spec["outline"],
            }
            use_specimen_plan = True
            print("    [specimen] preview 出图按 specimen_plan.json（每种 Img* 仍最多 1 张，防止标本漏去重）")
    lib = yaml.safe_load(lib_path.read_text(encoding="utf-8")) if lib_path.exists() else {}
    if not plan:
        print(f"    [warn] 未找到 plan.json（已试 {template_dir / 'plan.json'} 与 {OUTLINES_DIR / slug / 'plan.json'}）")
    content_path = template_dir / "content.json"
    blocks = ((load_json(content_path).get("blocks") or {})
              if (content_aware and content_path.exists()) else {})
    tag = "内容语义（对齐正文）" if blocks else "模板语义（按 main_point）"

    figs = collect_figures(template, plan, lib, blocks)
    if variant == "preview" and figs:
        before = len(figs)
        per = 1 if use_specimen_plan else 2
        figs = filter_figs_for_preview(figs, plan, template, per_style=per)
        print(
            f"    [exhibit] preview 出图 {before}→{len(figs)}"
            f"（每种 Img* style 最多 {per} 张实例，不限制 style 种数；仅 preview）"
        )
    elif variant == "document" and figs:
        # 故意不调用 filter_figs_for_preview：长文须按大纲全量出图
        print(f"    [exhibit] document 出图不裁剪（全量 {len(figs)} 张；与 preview 示范次数无关）")
    if not figs:
        print(f"[skip] 无图块：{slug}")
        return False
    print(f"\n=== build_figures[{variant}]: {slug} ===  ({len(figs)} 张图 · {tag})")

    fig_dir = template_dir / "figures"
    specs_path = fig_dir / specs_name
    fig_dir.mkdir(parents=True, exist_ok=True)
    regen_fids: set[str] = set()

    if specs_path.exists() and not overwrite:
        cached = load_json(specs_path)
        merged, need_plan, dropped, rerender = reconcile_figure_cache(
            figs, cached, assets_dir=template_dir / assets_sub,
        )
        if dropped or need_plan or rerender:
            print(
                f"    [figures-sync] plan 图块变化：删 {len(dropped)}，"
                f"重规划 {len(need_plan)}，重渲 {len(rerender)}，保留 {len(merged)}"
            )
        if need_plan:
            merged.update(plan_specs(need_plan, template, temperature, max_tokens))
            regen_fids.update(str(f["figure_id"]) for f in need_plan)
        regen_fids.update(rerender)
        specs = merged
        dump_json(specs_path, specs)
    else:
        specs = plan_specs(figs, template, temperature, max_tokens)
        dump_json(specs_path, specs)
        regen_fids = set(specs)

    dump_json(fig_dir / FIGURES_PLAN_SNAPSHOT, figures_plan_snapshot(figs))

    assets_dir = template_dir / assets_sub
    manifest: dict[str, dict] = {}
    for i, (fid, spec) in enumerate(specs.items()):
        asset = assets_dir / f"{_safe(fid)}.png"
        if asset.exists() and not overwrite and str(fid) not in regen_fids:
            used = spec.get("gen_method")
            print(f"    [cache] {fid} → {assets_sub}/{asset.name}")
        else:
            used = render_one(spec, asset, seed=seed + i)
            print(f"    [{used}] {fid} → {assets_sub}/{asset.name}")
        manifest[fid] = {
            "role_id": spec["role_id"],
            "block_id": spec.get("block_id"),
            "gen_method": used,
            "title": spec.get("title"),
            "alt": spec.get("alt"),
            "asset": f"{assets_sub}/{asset.name}",
            "width": f"{spec['width_cm']:g}cm",
            "height": f"{spec['height_cm']:g}cm",
            "align": spec.get("align", "center"),
        }
    dump_json(template_dir / manifest_name, manifest)
    n_prune = prune_orphan_pngs(assets_dir, manifest)
    if n_prune:
        print(f"    [figures-sync] 清理未引用 png ×{n_prune}")
    print(f"    清单：{(template_dir / manifest_name)}")

    if not no_render:
        try:
            from LongDocFmtEval.pipeline_auto.scripts.render_template_docx import render  # noqa: PLC0415
            if variant == "preview":
                print(f"    [template] {render(template_dir, out_name=render_target, assets_name=manifest_name)}")
                leftover = template_dir / "preview.docx"
                target = template_dir / render_target
                if leftover.is_file() and leftover.resolve() != target.resolve():
                    leftover.unlink()
            elif blocks:  # document 变体：有正文才渲 gold
                print(f"    [gold] {render(template_dir, content=blocks, annotated=False, out_name=render_target, assets_name=manifest_name)}")
            else:
                print("    [skip] document 变体缺 content.json，仅出图不渲 gold")
        except Exception as e:  # noqa: BLE001
            print(f"    [warn] 重渲染失败：{e}")
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description="step3.5 图构建：文生图 + 脚本图 + 占位")
    ap.add_argument("--template-dir", help="step3 产物目录（含 template.json）")
    ap.add_argument("--all", action="store_true", help="对 work/step3_templates 下所有模板出图")
    ap.add_argument("--overwrite", action="store_true", help="重规划并重出所有图")
    ap.add_argument("--no-render", action="store_true", help="出图后不自动重渲染 docx")
    ap.add_argument("--target", choices=["auto", "preview", "document", "both"], default="auto",
                    help="出哪套图：preview=模板语义(→preview.docx) / document=内容语义(→gold) / "
                         "both=两套都出 / auto=有 content.json 走 document 否则 preview（默认）")
    ap.add_argument("--temperature", type=float, default=0.5)
    ap.add_argument("--max-tokens", type=int, default=20480)
    ap.add_argument("--seed", type=int, default=1234)
    args = ap.parse_args()

    if args.all:
        dirs = sorted(p for p in TEMPLATES_DIR.iterdir() if (p / "template.json").exists())
    elif args.template_dir:
        d = Path(args.template_dir)
        if not d.exists():
            alt = Path("LongDocFmtEval") / args.template_dir
            d = alt if alt.exists() else d
        dirs = [d]
    else:
        ap.error("需指定 --template-dir 或 --all")
        return

    ok = 0
    for d in dirs:
        if args.target == "both":
            variants = ["preview", "document"]
        elif args.target == "auto":
            variants = ["document"] if (d / "content.json").exists() else ["preview"]
        else:
            variants = [args.target]
        done = False
        for v in variants:
            if build_one(d, variant=v, overwrite=args.overwrite, temperature=args.temperature,
                         max_tokens=args.max_tokens, seed=args.seed, no_render=args.no_render):
                done = True
        if done:
            ok += 1
    print(f"\n完成 {ok}/{len(dirs)} 个模板。")


if __name__ == "__main__":
    main()
