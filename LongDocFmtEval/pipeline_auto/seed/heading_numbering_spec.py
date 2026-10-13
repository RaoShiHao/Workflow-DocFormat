"""Natural-language heading numbering rules for text_spec (no template.docx)."""
from __future__ import annotations

from typing import Any

from LongDocFmtEval.pipeline_auto.seed.heading_numbering import format_prefix, load_schemes

_LEVEL_ZH = {
    1: "一级标题",
    2: "二级标题",
    3: "三级标题",
    4: "四级标题",
    5: "五级标题",
    6: "六级标题",
    7: "七级标题",
}
_LEVEL_EN = {
    1: "Level-1 heading",
    2: "Level-2 heading",
    3: "Level-3 heading",
    4: "Level-4 heading",
    5: "Level-5 heading",
    6: "Level-6 heading",
    7: "Level-7 heading",
}


def _scheme_levels(scheme: dict[str, Any]) -> list[int]:
    raw = scheme.get("levels") or {}
    out: list[int] = []
    for key in raw:
        try:
            out.append(int(key))
        except (TypeError, ValueError):
            continue
    return sorted(out)


def _example_prefix(scheme: dict[str, Any], level: int, counters: list[int] | None = None) -> str:
    c = counters if counters is not None else [1] * level
    return format_prefix(scheme, level, c).rstrip()


def _reset_note(scheme: dict[str, Any], level: int, loc: str) -> str | None:
    """Second-branch example showing counters reset under a new parent."""
    if level < 2:
        return None
    first = _example_prefix(scheme, level, [1] * level)
    second_parent = _example_prefix(scheme, level, [2] + [1] * (level - 1))
    if first == second_parent:
        return None
    if loc == "en":
        return f"resets under next parent (e.g. `{second_parent}`)"
    return f"进入新上级后重新编号（例：`{second_parent}`）"


def render_numbering_spec_md(
    scheme_id: str,
    *,
    locale: str = "zh",
    document_numbered: bool = True,
    schemes: dict[str, Any] | None = None,
) -> str:
    """Markdown block for t_text / text_spec when template is absent."""
    loc = "en" if str(locale).lower().startswith("en") else "zh"
    schemes = schemes or load_schemes()
    scheme = schemes.get(scheme_id) or {}
    labels = _LEVEL_EN if loc == "en" else _LEVEL_ZH
    level_desc = scheme.get("level_descriptions") or {}
    title = str(scheme.get("title") or scheme_id)
    summary = str(scheme.get("summary") or "").strip()

    if not document_numbered:
        if loc == "en":
            return (
                "## Heading numbering\n\n"
                "Do **not** add hierarchical numbers to heading text in the main body. "
                "Front matter (cover, abstract, table of contents) and back matter "
                "(appendix) headings remain unnumbered."
            )
        return (
            "## 标题编号\n\n"
            "正文区标题**不要**在文本前添加层级编号。"
            "前置部分（封面、摘要、目录等）与后置附录区标题同样不编号。"
        )

    lines: list[str] = []
    if loc == "en":
        lines.append("## Heading numbering (main body only)")
        lines.append("")
        lines.append(
            f"Apply scheme **{scheme_id}** (*{title}*) to heading **text** in the main body "
            "(prepend to the title; do not use Word list numbering). "
            "Do not number headings in front/back matter."
        )
    else:
        lines.append("## 标题编号（仅正文区）")
        lines.append("")
        lines.append(
            f"正文区段落标题按 **{scheme_id}**（{title}）在标题文本前添加编号前缀"
            "（写入标题文字，不使用 Word 自动编号）。"
            "封面、摘要、目录及附录等前置/后置节标题不编号。"
        )
    if summary:
        lines.append("")
        lines.append(summary)
    lines.append("")
    if loc == "en":
        lines.append(
            "**Counter rule:** each heading level maintains its own counter; "
            "when a higher level advances, all deeper counters reset to 1."
        )
    else:
        lines.append(
            "**计数规则：** 每个标题层级独立计数；当上层标题进入下一序号时，"
            "其下所有更深层级计数从初始值重新开始。"
        )
    lines.append("")
    for lv in _scheme_levels(scheme):
        ex = _example_prefix(scheme, lv)
        desc = str(level_desc.get(lv) or level_desc.get(str(lv)) or "").strip()
        reset = _reset_note(scheme, lv, loc)
        if loc == "en":
            line = f"- **{labels[lv]}**: "
            if desc:
                line += f"{desc}. "
            line += f"Example prefix: `{ex}`"
            if reset:
                line += f" ({reset})"
        else:
            line = f"- **{labels[lv]}**："
            if desc:
                line += f"{desc}。"
            line += f"示例前缀「{ex}」"
            if reset:
                line += f"（{reset}）"
        lines.append(line)
    lines.append("")
    if loc == "en":
        lines.append(
            "When the init document has no numbering, add these prefixes to reach the "
            "target; keep existing body text after the prefix unchanged."
        )
    else:
        lines.append(
            "若待调文档标题尚无编号，请按上表为正文区标题补上前缀；"
            "编号后的标题正文内容保持不变。"
        )
    return "\n".join(lines)


def render_numbering_reference_md(
    scheme_id: str | None,
    *,
    locale: str = "zh",
    schemes: dict[str, Any] | None = None,
) -> str:
    """Reference-only heading numbering rules (optional guidance, not a hard requirement)."""
    loc = "en" if str(locale).lower().startswith("en") else "zh"
    if not scheme_id:
        return ""
    schemes = schemes or load_schemes()
    if scheme_id not in schemes:
        return ""
    scheme = schemes[scheme_id]
    labels = _LEVEL_EN if loc == "en" else _LEVEL_ZH
    level_desc = scheme.get("level_descriptions") or {}
    title = str(scheme.get("title") or scheme_id)
    summary = str(scheme.get("summary") or "").strip()

    lines: list[str] = []
    if loc == "en":
        lines.append(
            "The following describes a **reference** heading-numbering pattern "
            f"({scheme_id}, {title}). It may or may not apply to this document; "
            "use only when consistent with the target golden document."
        )
    else:
        lines.append(
            f"以下为**仅供参考**的标题编号样式（{scheme_id}，{title}）。"
            "是否与成稿一致需自行判断；仅在与目标成稿匹配时采用。"
        )
    if summary:
        lines.append("")
        lines.append(summary)
    lines.append("")
    for lv in _scheme_levels(scheme):
        ex = _example_prefix(scheme, lv)
        desc = str(level_desc.get(lv) or level_desc.get(str(lv)) or "").strip()
        reset = _reset_note(scheme, lv, loc)
        if loc == "en":
            line = f"- **{labels[lv]}** (reference): "
            if desc:
                line += f"{desc}. "
            line += f"Example: `{ex}`"
            if reset:
                line += f" ({reset})"
        else:
            line = f"- **{labels[lv]}**（参考）："
            if desc:
                line += f"{desc}。"
            line += f"示例「{ex}」"
            if reset:
                line += f"（{reset}）"
        lines.append(line)
    return "\n".join(lines).strip()


def render_heading_level_note(
    scheme_id: str | None,
    level: int,
    *,
    display_name: str = "",
    locale: str = "zh",
    schemes: dict[str, Any] | None = None,
) -> str:
    """One-line numbering hint: ``{name} may look like `{prefix} …`.``

    Empty when the scheme has no template for ``level`` (do not invent notes).
    """
    if not scheme_id or level < 1:
        return ""
    loc = "en" if str(locale).lower().startswith("en") else "zh"
    schemes = schemes or load_schemes()
    scheme = schemes.get(scheme_id) or {}
    levels = scheme.get("levels") or {}
    if not (levels.get(level) or levels.get(str(level))):
        return ""
    # Still require a declared human rule so we don't annotate orphan levels.
    level_desc = scheme.get("level_descriptions") or {}
    if not str(level_desc.get(level) or level_desc.get(str(level)) or "").strip():
        return ""
    pref = _example_prefix(scheme, level).strip()
    if not pref:
        return ""
    name = str(display_name or "").strip() or (
        _LEVEL_EN.get(level, f"Heading {level}") if loc == "en"
        else _LEVEL_ZH.get(level, f"{level}级标题")
    )
    if loc == "en":
        return f"{name} may look like `{pref} …`."
    return f"{name}可形如「{pref} …」。"
