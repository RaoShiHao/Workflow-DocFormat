"""Per-sample table render overrides (step5 template.docx + step6 document.docx).

Humans mark samples where **cover / project info tables** must not be
rule-synthesized, or rely on the default rule: step6 never falls back to
rule-filled tables when ``content.json`` has no ``table`` payload for that block.

Write ``C*/step5_template/render_overrides.json`` or use
``scripts/set_render_overrides.py``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

RENDER_OVERRIDES_NAME = "render_overrides.json"


def resolve_render_overrides_path(work_dir: Path) -> Path | None:
    """Prefer ``work_dir``; step6 falls back to sibling ``step5_template/``."""
    wd = Path(work_dir)
    for d in (wd, wd.parent / "step5_template"):
        p = d / RENDER_OVERRIDES_NAME
        if p.is_file():
            return p
    return None


def load_render_overrides(work_dir: Path) -> dict[str, Any]:
    path = resolve_render_overrides_path(work_dir)
    if not path:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_render_overrides(template_dir: Path, data: dict[str, Any]) -> Path:
    path = Path(template_dir) / RENDER_OVERRIDES_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def delete_render_overrides(template_dir: Path) -> bool:
    path = Path(template_dir) / RENDER_OVERRIDES_NAME
    if path.is_file():
        path.unlink()
        return True
    return False


def should_skip_table_render(
    overrides: dict[str, Any] | None,
    *,
    is_cover_info: bool = False,
    gold_mode: bool = False,
    has_table_content: bool = False,
) -> str | None:
    """Return a short skip reason, or ``None`` to render normally."""
    if overrides and overrides.get("skip_cover_info_tables") and is_cover_info:
        return "人工标记：跳过封面/项目信息表"
    if gold_mode and not has_table_content:
        return "step6 无 table 内容，不强制规则填表"
    return None
