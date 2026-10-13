"""Render step2 plan.json outline as a human-readable hierarchy tree.

Usage:
  python LongDocFmtEval/pipeline_auto/scripts/viz_outline.py <plan.json|outline-dir> [out.md]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from LongDocFmtEval.pipeline_auto.seed.heading_detect import heading_level  # noqa: E402
from LongDocFmtEval.pipeline_auto.seed.heading_contract import heading_index  # noqa: E402


def _kind_tag(role_id: str, display: str = "", index: dict | None = None) -> str:
    rid = role_id or ""
    if rid.startswith("Tbl") or "Table" in rid:
        return "表"
    if rid.startswith("Img") or "Image" in rid or "Figure" in rid and "Title" not in rid and "Note" not in rid:
        return "图"
    if "Caption" in rid or "TableTitle" in rid or "FigureTitle" in rid:
        return "题注"
    if "Toc" in rid or "TOC" in rid:
        return "目录"
    rec = (index or {}).get(rid) or {}
    if rec.get("title_kind") == "cover":
        return "封面题"
    if rec.get("title_kind") == "subtitle":
        return "副题"
    hl = rec.get("heading_level") or heading_level(rid, display)
    if hl:
        return f"H{hl}"
    if rid.startswith("Sec"):
        return "节"
    return "段"


def _short(text: str, n: int = 72) -> str:
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    return t if len(t) <= n else t[: n - 1] + "…"


def _ov_summary(ov: dict | None) -> str:
    if not isinstance(ov, dict) or not ov:
        return ""
    bits = []
    if "pageNumFmt" in ov:
        bits.append(f"页码制式={ov['pageNumFmt']}")
    if "pageStart" in ov:
        bits.append(f"起始={ov['pageStart']}")
    if ov.get("titlePage"):
        bits.append("首页不同")
    return (" · " + "，".join(bits)) if bits else ""


def render_outline_tree(plan: dict) -> str:
    """Build markdown tree: sections → heading nest → leaf blocks."""
    meta = plan.get("meta") if isinstance(plan.get("meta"), dict) else {}
    sections = {s.get("instance_id"): s for s in (plan.get("sections") or []) if isinstance(s, dict)}
    outline = plan.get("outline") or []
    name_by: dict[str, str] = {}
    for item in plan.get("stylesheet") or []:
        if isinstance(item, dict) and item.get("style_id") and item.get("display_name"):
            name_by[str(item["style_id"])] = str(item["display_name"])
    idx = heading_index(plan)

    lines: list[str] = [
        "# 写作大纲树（blocks）",
        "",
        f"- **主题**：{meta.get('theme') or '（无）'}",
        f"- **体裁**：{meta.get('genre') or '（无）'} / {meta.get('locale') or ''}",
        f"- **theme_key**：{meta.get('theme_key') or ''}",
        "",
        "> 节 = Word section 实例；H1–H4 = 标题层级；其下缩进为从属内容块。",
        "",
    ]

    n_sec = 0
    n_blk = 0
    for part in outline:
        if not isinstance(part, dict):
            continue
        inst = part.get("instance_id") or "?"
        sec = sections.get(inst) or {}
        title = sec.get("title") or part.get("title") or inst
        role = sec.get("role_id") or part.get("role_id") or ""
        purpose = sec.get("purpose") or ""
        ov = sec.get("instance_overrides") or {}
        n_sec += 1
        lines.append(f"## {inst} · {title}")
        lines.append(f"- **节角色**：`{role}`{_ov_summary(ov)}")
        if purpose:
            lines.append(f"- **用途**：{purpose}")
        lines.append("")

        # nest under current heading depth; section root depth = 0
        # indent spaces = 2 * (display_depth)
        # For headings at level L, display_depth = L; children get L+1 until next heading <= L
        stack_levels: list[int] = [0]  # heading levels that are open; 0 = section root

        for b in part.get("blocks") or []:
            if not isinstance(b, dict):
                continue
            n_blk += 1
            bid = b.get("block_id") or "?"
            brid = b.get("role_id") or "?"
            dn = name_by.get(str(brid), "")
            point = _short(b.get("main_point") or "")
            hint = b.get("length_hint") or ""
            tag = _kind_tag(brid, dn, idx)
            rec = idx.get(str(brid)) or {}
            hl = rec.get("heading_level")
            if hl is None and not rec.get("title_kind"):
                hl = heading_level(brid, dn)

            if hl is not None:
                while len(stack_levels) > 1 and stack_levels[-1] >= hl:
                    stack_levels.pop()
                depth = hl  # H1 under section → depth 1
                stack_levels.append(hl)
                indent = "  " * depth
                extra = f" · {hint}" if hint else ""
                lines.append(f"{indent}- **[{tag}]** `{bid}` `{brid}` — {point}{extra}")
            else:
                # nest under deepest open heading, or directly under section
                parent_hl = stack_levels[-1]
                depth = parent_hl + 1 if parent_hl > 0 else 1
                indent = "  " * depth
                extra = f" · {hint}" if hint else ""
                lines.append(f"{indent}- [{tag}] `{bid}` `{brid}` — {point}{extra}")

        lines.append("")

    lines += [
        "---",
        f"合计：**{n_sec}** 个节实例，**{n_blk}** 个内容块。",
        "",
    ]
    return "\n".join(lines)


def write_outline_tree(plan: dict, out_path: Path) -> Path:
    out_path = Path(out_path)
    out_path.write_text(render_outline_tree(plan), encoding="utf-8")
    return out_path


def load_plan(path: Path) -> dict:
    path = Path(path)
    if path.is_dir():
        path = path / "plan.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"plan 不是对象: {path}")
    return data


def main():
    ap = argparse.ArgumentParser(description="将 step2 plan.json 的 outline/blocks 渲染为树形 Markdown")
    ap.add_argument("src", help="plan.json 或 step2 产出目录")
    ap.add_argument("out", nargs="?", help="输出 md（默认同目录 outline_tree.md）")
    args = ap.parse_args()
    src = Path(args.src)
    plan = load_plan(src)
    out = Path(args.out) if args.out else (
        (src / "outline_tree.md") if src.is_dir() else (src.parent / "outline_tree.md")
    )
    write_outline_tree(plan, out)
    print(f"[outline-tree] {out}")


if __name__ == "__main__":
    main()
