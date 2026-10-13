"""Named table chrome **reference** presets (not a closed enum).

These are sample mappings from「常见观感」→ officecli ``table_format`` / ``cells``.
Callers (step3 LLM, humans, render enrich) should:

  * pick one that fits the table's role in the document, **or**
  * remix keys (e.g. three-line + header fill), **or**
  * invent a new combination and describe it in natural language.

Render may use a preset only to **fill missing** 三线/斑马 row slots — never to
overwrite an intentionally designed ``cells`` block.

Semantic positions:
  - ``header`` — first **row** (column titles), never a whole column
  - ``stub`` / aliases — first **column** of body rows (row labels); optional
  - ``data`` / ``note`` / … — remaining body cells
  - ``label`` / ``value`` — 2-col info form (not a header-row table)
  - ``photo`` — image slot (often valign bottom)
  - ``row_first`` / ``row_second`` / ``row_last`` / ``row_odd`` / ``row_even`` — row-index chrome
"""
from __future__ import annotations

import re
from typing import Any

# ── Reference catalog (officecli-oriented samples) ───────────────────────────

PRESETS: dict[str, dict[str, Any]] = {
    # (1) 普通网格表
    "grid_full": {
        "label": "普通表格（全网格）",
        "table_format": {
            "border.all": "single;4;auto",
            "align": "center",
            "width": "100%",
            "repeat_header": True,
        },
        "cells": {
            "header": {
                "border.all": "single;4;auto",
                "fill": "#E7E6E6",
                "valign": "center",
            },
            "stub": {"border.all": "single;4;auto", "valign": "center"},
            "data": {"border.all": "single;4;auto", "valign": "center"},
            "note": {"border.all": "single;4;auto", "valign": "top"},
        },
    },
    # (2) 三线表 — 以人工改过的 Word 实测为准（officecli get /body/table[2]）：
    #   表级无框；线全部画在「行位」单元格上，无竖线。
    #   第1行：上粗(12) + 下细(4)；第2行：上细(4)；中间无框；末行：下粗(12)
    "three_line": {
        "label": "三线表",
        "table_format": {
            "border.all": "none",
            "align": "center",
            "width": "100%",
            "repeat_header": True,
        },
        "cells": {
            "header": {
                "border.top": "single;12;auto",
                "border.bottom": "single;4;auto",
                "border.left": "nil",
                "border.right": "nil",
                "valign": "center",
            },
            "stub": {"border.all": "none", "valign": "center"},
            "data": {"border.all": "none", "valign": "center"},
            "note": {"border.all": "none", "valign": "top"},
            "remark": {"border.all": "none", "valign": "top"},
            "row_second": {
                "border.top": "single;4;auto",
                "border.left": "nil",
                "border.right": "nil",
                "border.bottom": "nil",
            },
            "row_last": {
                "border.bottom": "single;12;auto",
                "border.left": "nil",
                "border.right": "nil",
                "border.top": "nil",
            },
        },
    },
    # (3) 信息表（2 列键值，值列下划线）
    "info_underline": {
        "label": "信息表（键值·值列底线）",
        "table_format": {
            "border.all": "none",
            "align": "center",
            "layout": "autofit",
        },
        "cells": {
            "label": {
                "border.all": "none",
                "valign": "center",
            },
            "value": {
                "border.bottom": "single;4;auto",
                "border.top": "nil",
                "border.left": "nil",
                "border.right": "nil",
                "valign": "center",
            },
        },
    },
    # (4) 人员/照片信息表
    "personnel_photo": {
        "label": "特殊信息表（含照片位）",
        "table_format": {
            "border.all": "single;4;auto",
            "align": "center",
            "layout": "autofit",
        },
        "cells": {
            "photo": {
                "border.all": "single;4;auto",
                "valign": "bottom",
            },
            "header": {
                "border.all": "single;4;auto",
                "fill": "#F2F2F2",
                "valign": "center",
            },
            "label": {
                "border.all": "single;4;auto",
                "fill": "#F2F2F2",
                "valign": "center",
            },
            "person_value": {
                "border.all": "single;4;auto",
                "valign": "center",
            },
            "data": {"border.all": "single;4;auto", "valign": "center"},
        },
    },
    # (5a) 表头底纹 + 无边框
    "header_shade_open": {
        "label": "表头底纹（无边框）",
        "table_format": {
            "border.all": "none",
            "align": "center",
            "width": "100%",
            "repeat_header": True,
        },
        "cells": {
            "header": {
                "border.all": "none",
                "fill": "#D9E2F3",
                "valign": "center",
            },
            "stub": {"border.all": "none", "valign": "center"},
            "data": {"border.all": "none", "valign": "center"},
            "note": {"border.all": "none", "valign": "top"},
        },
    },
    # (5b) 斑马纹（奇偶行底纹，无边框）
    "zebra_open": {
        "label": "斑马纹（无边框）",
        "table_format": {
            "border.all": "none",
            "align": "center",
            "width": "100%",
            "repeat_header": True,
        },
        "cells": {
            "header": {
                "border.all": "none",
                "fill": "#D0D0D0",
                "valign": "center",
            },
            "stub": {"border.all": "none", "valign": "center"},
            "data": {"border.all": "none", "valign": "center"},
            "note": {"border.all": "none", "valign": "top"},
            "row_odd": {"fill": "#F2F2F2"},
            "row_even": {"fill": "auto"},
        },
    },
    # 发散：(6) 表头底纹 + 全网格
    "header_shade_grid": {
        "label": "表头底纹+全网格",
        "table_format": {
            "border.all": "single;4;auto",
            "align": "center",
            "width": "100%",
            "repeat_header": True,
        },
        "cells": {
            "header": {
                "border.all": "single;4;auto",
                "fill": "#DCE6F1",
                "valign": "center",
            },
            "stub": {"border.all": "single;4;auto", "valign": "center"},
            "data": {"border.all": "single;4;auto", "valign": "center"},
            "note": {"border.all": "single;4;auto", "valign": "top"},
        },
    },
    # 发散：(7) 顶底双线（学术简表，弱于三线）
    "top_bottom_only": {
        "label": "顶底线表",
        "table_format": {
            "border.top": "single;8;auto",
            "border.bottom": "single;8;auto",
            "border.left": "nil",
            "border.right": "nil",
            "align": "center",
            "width": "100%",
            "repeat_header": True,
        },
        "cells": {
            "header": {
                "border.bottom": "single;4;auto",
                "valign": "center",
            },
            "stub": {"border.all": "none", "valign": "center"},
            "data": {"border.all": "none", "valign": "center"},
            "note": {"border.all": "none", "valign": "top"},
        },
    },
}


def catalog_for_prompt() -> str:
    """Human-readable reference catalog for step3 (emphasize soft guidance)."""
    lines = [
        "表框参考目录（样板，非封闭枚举；按该表语义选用、改写或自拟新观感）：",
    ]
    for key, preset in PRESETS.items():
        lines.append(f"  - 参考 `{key}` · {preset['label']}")
    lines.extend([
        "用法：输出具体 table_format + cells，不必输出预设 id；可混搭键、可新建。",
        "硬规则仅：多列表头=第一行；同文表观感宜可区分；照片位可 valign=bottom。",
        "行位可叠加：row_second/row_last（三线）、row_odd/row_even（斑马）。",
    ])
    return "\n".join(lines)


def infer_preset_name(tbl_spec: dict[str, Any] | None) -> str | None:
    """Guess preset from existing table_format / cells (for enrich)."""
    from LongDocFmtEval.pipeline_auto.seed.seed_ir import (  # noqa: PLC0415
        table_cells_chrome_of,
        table_format_of,
    )

    tf = table_format_of(tbl_spec)
    cells = table_cells_chrome_of(tbl_spec)
    if "photo" in cells or "photo_slot" in cells:
        return "personnel_photo"
    if "label" in cells and "value" in cells and "header" not in cells:
        return "info_underline"
    if tf.get("border.all") in ("none", None) and (
        cells.get("row_odd") or cells.get("row_even")
    ):
        return "zebra_open"
    if tf.get("border.all") == "none" or (
        not tf.get("border.all")
        and (tf.get("border.top") or tf.get("border.bottom"))
        and not any(str(k).startswith("border.") and "all" in k for k in tf)
    ):
        # top/bottom structure without full grid → three_line-ish
        if cells.get("header", {}).get("fill"):
            return "header_shade_open"
        return "three_line"
    if cells.get("header", {}).get("fill") and tf.get("border.all"):
        return "header_shade_grid"
    if tf.get("border.all"):
        return "grid_full"
    return None


def enrich_cells_chrome(
    tbl_spec: dict[str, Any] | None,
    *,
    preset_name: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Return cells chrome, filling missing 三线/斑马 row slots from preset."""
    from LongDocFmtEval.pipeline_auto.seed.build_table_samples import (  # noqa: PLC0415
        normalize_rc_chrome,
    )
    from LongDocFmtEval.pipeline_auto.seed.seed_ir import (  # noqa: PLC0415
        table_cells_chrome_of,
        table_format_of,
    )

    tf = table_format_of(tbl_spec) if tbl_spec else {}
    cells = normalize_rc_chrome(
        {k: dict(v) for k, v in table_cells_chrome_of(tbl_spec).items()},
        table_format=tf,
    )
    # Re-infer preset on normalized keys (rNcM cover → info_underline, not three_line)
    if preset_name is None and tbl_spec is not None:
        patched = dict(tbl_spec)
        props = dict(patched.get("props") or {})
        props["cells"] = cells
        patched["props"] = props
        patched["cells"] = cells
        name = infer_preset_name(patched)
    else:
        name = preset_name or infer_preset_name(tbl_spec)

    if not name or name not in PRESETS:
        return cells
    preset_cells = PRESETS[name].get("cells") or {}
    # Only fill missing keys / missing border slots — don't clobber LLM choices
    for pos, chrome in preset_cells.items():
        if pos not in cells:
            cells[pos] = dict(chrome)
            continue
        if pos.startswith("row_") and not any(
            str(k).startswith("border.") or k == "fill" for k in cells[pos]
        ):
            cells[pos] = {**chrome, **cells[pos]}
        elif name == "three_line" and pos in ("row_second", "row_last") and pos not in cells:
            cells[pos] = dict(chrome)
    if name == "info_underline":
        # Shared ParaTblHeaderCell chrome from grid tables must not stick on
        # label/value (fill + full borders). Keep only valign from extract.
        for pos in ("label", "value"):
            preset = dict(preset_cells.get(pos) or {})
            cur = cells.get(pos) or {}
            if cur.get("valign"):
                preset["valign"] = cur["valign"]
            cells[pos] = preset
    if name == "three_line":
        cells.setdefault("row_second", dict(preset_cells.get("row_second") or {}))
        cells.setdefault("row_last", dict(preset_cells.get("row_last") or {}))
        # LLM often puts border.top on note (=footnote band); in grids note is a
        # normal column → paints a rule on every body row. Force clear.
        for pos in ("note", "remark", "stub", "data", "attribute", "responsibility",
                    "project_name", "task", "detail", "item"):
            if pos in cells:
                cells[pos] = {
                    k: v for k, v in cells[pos].items()
                    if not str(k).startswith("border.")
                }
                cells[pos]["border.all"] = "none"
                if "valign" not in cells[pos]:
                    cells[pos]["valign"] = "top" if pos in ("note", "remark") else "center"
        if "header" in cells:
            h = dict(cells["header"])
            for k in list(h):
                if str(k).startswith("border."):
                    h.pop(k, None)
            h["border.top"] = "single;12;auto"
            h["border.bottom"] = "single;4;auto"
            h["border.left"] = "nil"
            h["border.right"] = "nil"
            cells["header"] = h
    return cells


def chrome_for_declared_style(
    chrome_by_pos: dict[str, dict[str, Any]],
    cell_style: str,
) -> dict[str, Any]:
    """Lookup Tbl*.cells[cell_style] only — no row-index overlays."""
    key = resolve_cell_style_key(cell_style, chrome_by_pos)
    return dict(chrome_by_pos.get(key) or chrome_by_pos.get(cell_style) or {})


def cell_style_plan_of(tbl_spec: dict[str, Any] | None) -> dict[str, Any] | None:
    """Optional LLM plan: how physical cells pick cell_style keys.

    Supported shapes (on ``props.cell_style_plan`` or top-level):
      {"mode": "column"}   # default: semantic pos / column_styles if given
      {"mode": "column", "column_styles": ["stub", "data", "data"]}
      {"mode": "row", "row_styles": ["header", "row_second", "data", "row_last"]}
      {"mode": "matrix", "matrix": [["header","header"],["label","value"], ...]}
    """
    if not isinstance(tbl_spec, dict):
        return None
    props = tbl_spec.get("props") if isinstance(tbl_spec.get("props"), dict) else {}
    plan = props.get("cell_style_plan") or tbl_spec.get("cell_style_plan")
    return plan if isinstance(plan, dict) else None


_NOTE_STYLE_KEYS = frozenset({"note", "remark", "source", "footnote"})


def physical_body_slots(
    pmap: dict[str, str] | None,
    ncols: int,
    *,
    plan: dict[str, Any] | None = None,
) -> list[tuple[str, str]]:
    """Map LLM table style → one ``(cell_style, paragraph_id)`` per body column.

    Extra physical columns clone the repeating data/value style. ``note`` is a
    designed cell type, not “whatever is left in the last column”.
    Honors ``cell_style_plan.column_styles`` when the LLM declared it.
    """
    pmap = {str(k): str(v) for k, v in (pmap or {}).items() if k}
    ncols = max(1, int(ncols or 1))
    plan = plan if isinstance(plan, dict) else {}

    declared = plan.get("column_styles") or plan.get("columns")
    if isinstance(declared, list) and any(declared):
        keys = [str(x).strip() for x in declared if str(x).strip()]
        repeat = "data"
        for k in keys:
            if k.lower() not in _NOTE_STYLE_KEYS:
                repeat = k
        while len(keys) < ncols:
            keys.append(repeat)
        out: list[tuple[str, str]] = []
        for k in keys[:ncols]:
            para = pmap.get(k) or (
                "ParaTblStubCell" if k in ("stub", "label") else "ParaTblDataCell"
            )
            out.append((k, para))
        return out

    stub_key = "stub" if "stub" in pmap else ("label" if "label" in pmap else None)
    data_key = "data" if "data" in pmap else ("value" if "value" in pmap else "data")
    data_para = pmap.get(data_key) or "ParaTblDataCell"
    stub_para = pmap.get(stub_key) if stub_key else None
    slots: list[tuple[str, str]] = []
    for i in range(ncols):
        if i == 0 and stub_key:
            slots.append((stub_key, str(stub_para or "ParaTblStubCell")))
        else:
            slots.append((data_key, str(data_para)))
    return slots


def llm_cells_chrome(tbl_spec: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Cells chrome **as designed by LLM** (normalize only; no three-line bake-in)."""
    from LongDocFmtEval.pipeline_auto.seed.build_table_samples import (  # noqa: PLC0415
        normalize_rc_chrome,
    )
    from LongDocFmtEval.pipeline_auto.seed.seed_ir import (  # noqa: PLC0415
        table_cells_chrome_of,
        table_format_of,
    )

    tf = table_format_of(tbl_spec) if tbl_spec else {}
    return normalize_rc_chrome(
        {k: dict(v) for k, v in table_cells_chrome_of(tbl_spec).items()},
        table_format=tf,
    )


def assign_declared_cell_styles(
    grid: list[list[tuple]],
    tbl_spec: dict[str, Any] | None,
) -> list[list[tuple[str, str, str]]]:
    """Normalize grid to (cell_style, paragraph_style, text) using LLM plan or column pos.

    Input cell may be:
      (pos, para, text) — pos used as default cell_style
      (cell_style, para, text) — already declared
      dict with cell_style / paragraph_style / pos / role_id / text
    """
    plan = cell_style_plan_of(tbl_spec) or {"mode": "column"}
    mode = str(plan.get("mode") or "column").lower().strip()
    row_styles = [str(x) for x in (plan.get("row_styles") or []) if x]
    matrix = plan.get("matrix") if isinstance(plan.get("matrix"), list) else None

    out: list[list[tuple[str, str, str]]] = []
    for ri, row in enumerate(grid):
        if not isinstance(row, list):
            continue
        new_row: list[tuple[str, str, str]] = []
        for ci, cell in enumerate(row):
            if isinstance(cell, dict):
                text = str(cell.get("text") or "")
                para = str(
                    cell.get("paragraph_style")
                    or cell.get("role_id")
                    or cell.get("para_role")
                    or "ParaTblDataCell"
                )
                pos = str(cell.get("pos") or cell.get("cell_style") or "data")
                explicit = cell.get("cell_style")
            elif isinstance(cell, (list, tuple)) and len(cell) >= 3:
                # (pos|cell_style, paragraph_style, text) — first field is semantic default;
                # row/matrix plans may remap. Optional 4th ignored.
                pos, para, text = str(cell[0]), str(cell[1]), str(cell[2])
                explicit = None
            else:
                pos, para, text = "data", "ParaTblDataCell", str(cell or "")
                explicit = None

            if explicit:
                cs = str(explicit)
            elif mode == "matrix" and matrix and ri < len(matrix):
                mrow = matrix[ri] if isinstance(matrix[ri], list) else []
                cs = str(mrow[ci] if ci < len(mrow) else (mrow[-1] if mrow else pos))
            elif mode == "row" and row_styles:
                cs = row_styles[ri] if ri < len(row_styles) else row_styles[-1]
            elif mode == "column":
                col_styles = plan.get("column_styles") or plan.get("columns")
                if isinstance(col_styles, list) and col_styles:
                    keys = [str(x).strip() for x in col_styles if str(x).strip()]
                    if ci < len(keys):
                        cs = keys[ci]
                    else:
                        repeat = keys[-1]
                        for k in keys:
                            if k.lower() not in _NOTE_STYLE_KEYS:
                                repeat = k
                        cs = repeat
                else:
                    cs = pos
            else:
                cs = pos
            new_row.append((cs, para, str(text)))
        out.append(new_row)
    return out


def resolve_cell_style_key(
    pos: str,
    chrome_by_pos: dict[str, dict[str, Any]] | None = None,
) -> str:
    """Map a physical grid cell id → a defined **table-cell style** key.

    One table style = structure layer + cell layer. Cell styles are design-time
    ids (by row, by column, or mixed). Extra body columns synthesized as
    ``data_2`` / ``data_3`` / … must still resolve to a real cell style
    (usually ``data``) so every physical cell is controlled.
    """
    pos = str(pos or "").strip() or "data"
    chrome = chrome_by_pos or {}
    if pos in chrome:
        return pos

    m = re.match(r"^(data|value|stub|note|label|header)(?:_\d+)?$", pos, flags=re.I)
    if m:
        stem = m.group(1).lower()
        if stem in chrome:
            return stem
        aliases = {
            "data": ("data", "value", "person_value", "field_value"),
            "value": ("value", "data", "person_value", "field_value"),
            "stub": ("stub", "label", "data"),
            "note": ("note", "data", "value"),
            "label": ("label", "stub", "header"),
            "header": ("header", "label"),
        }
        for cand in aliases.get(stem, (stem,)):
            if cand in chrome:
                return cand

    if re.match(r"^col_\d+$", pos) or pos in ("col", "body", "cell"):
        for cand in ("data", "value", "stub", "note"):
            if cand in chrome:
                return cand

    for key in chrome:
        if str(key).startswith("row_"):
            continue
        if pos.startswith(str(key)) or str(key).startswith(pos):
            return str(key)

    for cand in ("data", "value", "stub", "label", "header", "note"):
        if cand in chrome:
            return cand
    return pos


def merge_row_slot_chrome(
    chrome_by_pos: dict[str, dict[str, Any]],
    pos: str,
    *,
    row_i: int,
    n_rows: int,
) -> dict[str, Any]:
    """Deprecated for render: prefer :func:`chrome_for_declared_style`.

    Kept for legacy callers; still merges row_* by index (avoid in new paths).
    """
    key = resolve_cell_style_key(pos, chrome_by_pos)
    base = dict(chrome_by_pos.get(key) or chrome_by_pos.get(pos) or {})
    if row_i == 1 and chrome_by_pos.get("row_first"):
        base = {**dict(chrome_by_pos["row_first"]), **base}
    if row_i == 2 and chrome_by_pos.get("row_second"):
        rs = chrome_by_pos["row_second"]
        if any(str(k).startswith("border.") for k in rs):
            base.pop("border.all", None)
        base.update(rs)
    if n_rows > 1 and row_i == n_rows and chrome_by_pos.get("row_last"):
        rl = chrome_by_pos["row_last"]
        if any(str(k).startswith("border.") for k in rl):
            base.pop("border.all", None)
        base.update(rl)
    # zebra: body rows only (row_i > 1)
    if row_i > 1:
        if row_i % 2 == 1 and chrome_by_pos.get("row_odd"):
            base = {**base, **dict(chrome_by_pos["row_odd"])}
        elif row_i % 2 == 0 and chrome_by_pos.get("row_even"):
            base = {**base, **dict(chrome_by_pos["row_even"])}
    return base
