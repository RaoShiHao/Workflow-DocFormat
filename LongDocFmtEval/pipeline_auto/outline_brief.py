"""Build compact outline briefs for cross-outline diversity in pipeline_auto."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from LongDocFmtEval.pipeline_auto.seed.heading_detect import heading_level
from LongDocFmtEval.pipeline_auto.seed.heading_contract import heading_index


def _style_maps(plan: dict) -> tuple[dict[str, str], dict[str, str]]:
    name_by: dict[str, str] = {}
    obj_by: dict[str, str] = {}
    for item in plan.get("stylesheet") or []:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("style_id") or item.get("role_id") or "")
        if not sid:
            continue
        if item.get("display_name"):
            name_by[sid] = str(item["display_name"])
        if item.get("object"):
            obj_by[sid] = str(item["object"])
    return name_by, obj_by


def build_outline_brief(plan: dict, *, outline_id: str = "") -> dict[str, Any]:
    """Compact structural fingerprint — safe to inject into the next outline prompt."""
    name_by, obj_by = _style_maps(plan)
    sec_names: list[str] = []
    for sec in plan.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        rid = str(sec.get("role_id") or "")
        sec_names.append(name_by.get(rid) or rid or "?")

    heading_levels: Counter[str] = Counter()
    table_kinds: Counter[str] = Counter()
    image_kinds: Counter[str] = Counter()
    n_blocks = 0
    idx = heading_index(plan)
    for o in plan.get("outline") or []:
        for b in o.get("blocks") or []:
            if not isinstance(b, dict):
                continue
            n_blocks += 1
            rid = str(b.get("role_id") or "")
            obj = obj_by.get(rid) or ""
            dn = name_by.get(rid) or rid
            rec = idx.get(rid) or {}
            hl = rec.get("heading_level")
            if hl is None and not rec.get("title_kind"):
                hl = heading_level(rid, dn)
            if hl:
                heading_levels[f"H{hl}"] += 1
            if obj == "table" or rid.startswith("Tbl"):
                table_kinds[dn] += 1
            if obj == "image" or rid.startswith("Img"):
                image_kinds[dn] += 1

    has_cover = any("封面" in s for s in sec_names)
    has_toc = any("目录" in s for s in sec_names)
    has_appendix = any(("附录" in s or "附件" in s) for s in sec_names)
    has_landscape = any(("横向" in s or "landscape" in s.lower()) for s in sec_names)

    return {
        "outline_id": outline_id,
        "section_sequence": sec_names,
        "heading_counts": dict(heading_levels),
        "table_styles_used": dict(table_kinds),
        "image_styles_used": dict(image_kinds),
        "n_sections": len(sec_names),
        "n_blocks": n_blocks,
        "flags": {
            "cover": has_cover,
            "toc": has_toc,
            "appendix": has_appendix,
            "landscape_insert": has_landscape,
        },
        "structure_tag": _structure_tag(
            has_cover, has_toc, has_appendix, has_landscape, table_kinds, heading_levels
        ),
    }


def _structure_tag(
    cover: bool,
    toc: bool,
    appendix: bool,
    landscape: bool,
    tables: Counter[str],
    headings: Counter[str],
) -> str:
    bits = []
    bits.append("有封面" if cover else "无封面")
    bits.append("有目录" if toc else "无目录")
    bits.append("有附录/附件" if appendix else "无附录")
    if landscape:
        bits.append("含横向插页")
    if tables:
        bits.append("表型:" + "/".join(list(tables.keys())[:4]))
    depth = max((1 for k in headings if k.startswith("H")), default=0)
    if headings.get("H4"):
        depth = 4
    elif headings.get("H3"):
        depth = 3
    elif headings.get("H2"):
        depth = 2
    elif headings.get("H1"):
        depth = 1
    bits.append(f"标题深≈{depth}")
    return "；".join(bits)


def brief_to_prompt_block(brief: dict) -> str:
    lines = [
        f"- id: {brief.get('outline_id') or '?'}",
        f"  结构标签: {brief.get('structure_tag') or ''}",
        f"  节序列: {' → '.join(brief.get('section_sequence') or [])}",
        f"  标题计数: {brief.get('heading_counts') or {}}",
        f"  表样式: {brief.get('table_styles_used') or {}}",
        f"  图样式: {brief.get('image_styles_used') or {}}",
        f"  flags: {brief.get('flags') or {}}",
    ]
    return "\n".join(lines)


def briefs_to_prompt(briefs: list[dict]) -> str:
    if not briefs:
        return ""
    parts = ["已有大纲摘要："]
    for b in briefs:
        parts.append(brief_to_prompt_block(b))
    return "\n".join(parts)


def write_outline_brief(plan: dict, out_dir: Path, *, outline_id: str = "") -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    brief = build_outline_brief(plan, outline_id=outline_id or out_dir.name)
    path = out_dir / "outline_brief.json"
    path.write_text(json.dumps(brief, ensure_ascii=False, indent=2), encoding="utf-8")
    md = out_dir / "outline_brief.md"
    md.write_text(
        "# Outline brief\n\n```text\n" + brief_to_prompt_block(brief) + "\n```\n",
        encoding="utf-8",
    )
    return path


def load_outline_brief(outline_dir: Path) -> dict | None:
    p = outline_dir / "outline_brief.json"
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001
        return None
