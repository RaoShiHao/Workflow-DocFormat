"""Write human-reviewable table/image style differentiation notes from step2 plan."""
from __future__ import annotations

from pathlib import Path
from typing import Any


def _collect_media_styles(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize media_style_diff; fall back to stylesheet table/image entries."""
    raw = plan.get("media_style_diff")
    items: list[dict[str, Any]] = []
    if isinstance(raw, list):
        for it in raw:
            if not isinstance(it, dict):
                continue
            sid = str(it.get("style_id") or it.get("role_id") or "").strip()
            if not sid:
                continue
            items.append({
                "style_id": sid,
                "object": str(it.get("object") or "").strip().lower() or "?",
                "differentiates_from": [
                    str(x) for x in (it.get("differentiates_from") or []) if x
                ],
                "reason": str(it.get("reason") or "").strip(),
            })
    if items:
        return items

    # Fallback: synthesize from stylesheet expectations (older plans)
    for st in plan.get("stylesheet") or []:
        if not isinstance(st, dict):
            continue
        obj = str(st.get("object") or "").lower()
        if obj not in ("table", "image"):
            continue
        sid = str(st.get("style_id") or "").strip()
        if not sid:
            continue
        reason = str(st.get("expectation") or st.get("description") or "").strip()
        items.append({
            "style_id": sid,
            "object": obj,
            "differentiates_from": [],
            "reason": reason or "(missing media_style_diff — using stylesheet expectation)",
        })
    return items


def write_media_style_diff(plan: dict[str, Any], path: Path) -> Path:
    """Write ``media_style_diff.md`` (+ ``.json`` sibling) for human review."""
    path = Path(path)
    items = _collect_media_styles(plan)
    tables = [x for x in items if x["object"] == "table"]
    images = [x for x in items if x["object"] == "image"]
    other = [x for x in items if x["object"] not in ("table", "image")]

    lines = [
        "# 表 / 图 style 区分理由（media_style_diff）",
        "",
        "供人工审阅：每个 **Tbl\\*** / **Img\\*** 命名样式为何单独存在（须是可观察的版式差异，",
        "而非「内容不同」）。template/preview 中 **每种 role 最多示范 2 次**，与 style 种数无关。",
        "",
        f"- 表 styles：{len(tables)}",
        f"- 图 styles：{len(images)}",
        "",
    ]

    def _section(title: str, rows: list[dict[str, Any]]) -> None:
        lines.append(f"## {title}")
        lines.append("")
        if not rows:
            lines.append("（无）")
            lines.append("")
            return
        for it in rows:
            vs = ", ".join(it["differentiates_from"]) if it["differentiates_from"] else "—"
            lines.append(f"### `{it['style_id']}`")
            lines.append("")
            lines.append(f"- **对照**: {vs}")
            lines.append(f"- **理由**: {it['reason'] or '（未填写）'}")
            lines.append("")

    _section("表格 (table)", tables)
    _section("图片 (image)", images)
    if other:
        _section("其他", other)

    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    try:
        from LongDocFmtEval.pipeline_auto.seed.seed_ir import dump_json  # noqa: PLC0415
        dump_json(path.with_suffix(".json"), {"media_style_diff": items})
    except Exception:  # noqa: BLE001
        pass
    return path
