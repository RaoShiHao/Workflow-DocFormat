"""Resolve LongDocFmtEval prompt YAML by locale (zh vs en)."""
from __future__ import annotations

from pathlib import Path


def norm_prompt_locale(locale: str) -> str:
    return "en" if str(locale or "").lower().startswith("en") else "zh"


def resolve_prompt_path(
    config_dir: Path,
    locale: str,
    name: str,
    *,
    fallbacks: tuple[str, ...] = (),
) -> Path:
    """Pick ``prompt_config/`` (zh) or ``prompt_config_en/`` (en).

    Order: requested locale tree for ``name`` then ``fallbacks``; if locale is en
    and nothing exists, fall back to the Chinese tree the same way.
    """
    config_dir = Path(config_dir)
    loc = norm_prompt_locale(locale)
    sub = "prompt_config_en" if loc == "en" else "prompt_config"
    names = (name, *fallbacks)
    for n in names:
        p = config_dir / sub / n
        if p.is_file():
            return p
    if loc == "en":
        for n in names:
            p = config_dir / "prompt_config" / n
            if p.is_file():
                return p
    return config_dir / sub / name


def format_theme_context(
    locale: str,
    *,
    genre: str = "",
    theme: str = "",
    writing_requirements: str = "",
    note: str = "",
) -> str:
    """Locale-matched genre/theme block injected into LLM user prompts (not system YAML)."""
    g = str(genre or "")
    t = str(theme or "")
    w = str(writing_requirements or "")
    n = str(note or "").strip()
    if norm_prompt_locale(locale) == "en":
        parts = [
            f"Genre: {g}",
            f"Theme: {t}",
            f"Writing requirements: {w}",
        ]
        if n:
            parts.append(f"Note: {n}")
        return "\n".join(parts)
    parts = [
        f"体裁 genre: {g}",
        f"主题 theme: {t}",
        f"写作要求 writing_requirements: {w}",
    ]
    if n:
        parts.append(f"说明：{n}")
    return "\n".join(parts)


def format_bibliography_context(locale: str, *, has_bibliography: bool) -> str:
    """Locale-matched bibliography guidance appended to step6 user context."""
    if norm_prompt_locale(locale) == "en":
        if has_bibliography:
            return (
                "\nCitations: this document has bibliography/reference roles — "
                "write superscript marks only where the outline's expected_runs require them; "
                "numbers must match bibliography [1][2]…; reuse the same number for the same source."
            )
        return (
            "\nCitations: this document has no bibliography/reference roles — "
            "do not invent superscript citation numbers or bibliography entries."
        )
    if has_bibliography:
        return (
            "\n文献引用（本篇有参考文献/书目）：正文仅在大纲 expected_runs 要求处写入上标角标，"
            "号与书目 [1][2]… 一致；同一来源复用同一号。"
        )
    return (
        "\n文献引用：本篇无参考文献/书目角色——禁止自行添加上标文献号或编造书目条目。"
    )
