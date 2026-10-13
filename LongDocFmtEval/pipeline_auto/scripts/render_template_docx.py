"""Render a step3 T* into a realistic .docx following the step2 outline.

Input  = a work dir with ``role_params.json`` (format) and optional ``template.json`` (names).
         plan.json / selected_library.yaml are read from the same dir when present
         (topic-tree copy), else work/step2_outlines/<dirname>/.
Output = <that dir>/preview.docx — a real document skeleton:
  - content objects (paragraphs / tables / images) are placed INSIDE their section
    per the outline, each with its officecli params applied;
  - every section carries its own header (the section's natural title) + footer (page
    number field) → "unlink from previous"; page numbering restart/format follow the
    instance overrides (pageStart / pageNumFmt / titlePage);
  - headings pull their title text from the outline block; body/table/image use the
    role's example text / block main_point;
  - also refreshes seed-compatible ``map.json`` / ``styles.json`` (paragraph / table
    indices) for human edit + sync_map_from_markers.

Usage:
  python LongDocFmtEval/pipeline_auto/scripts/render_template_docx.py --template-dir "LongDocFmtEval/pipeline_auto/work/step3_templates/<slug>"
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from LongDocFmtEval.common.llm import load_json
from LongDocFmtEval.common.officecli_runner import (  # noqa: E402
    add_props,
    close_docx,
    create_docx,
    flush_officecli_batch,
    officecli_batch_session,
    set_props,
    validate_docx,
)
from LongDocFmtEval.pipeline_auto.seed.build_table_samples import (
    build_grid_for_table,
    parse_pipe_matrix,
)
from LongDocFmtEval.pipeline_auto.seed.seed_ir import (
    SECTION_OFFICECLI_KEYS,
    _normalize_border_value,
    apply_render_prop_guards,
    coerce_officecli_prop_keys,
    dump_json,
    expand_font_props,
    font_family_of,
    strip_para_marker,
    strip_wrong_layer_keys,
    style_props_for_paragraph,
    table_format_of,
    with_para_marker,
)
from LongDocFmtEval.pipeline_auto.seed.table_chrome_presets import (
    assign_declared_cell_styles,
    cell_style_plan_of,
    chrome_for_declared_style,
    llm_cells_chrome,
    physical_body_slots,
    resolve_cell_style_key,
)
from LongDocFmtEval.pipeline_auto.seed.plan_sanitize import (
    apply_section_page_continuity,
    drop_section_role_blocks,
    is_section_style_block,
    prune_empty_sections,
    strip_abstract_roles,
)
from LongDocFmtEval.pipeline_auto.seed.template_exhibits import (
    plan_template_exhibits,
    should_skip_block,
)


def cell_text_props(props: dict) -> dict:
    """Legacy: Para*Cell props → direct cell text attributes.

    Table render now applies named paragraph styles on the inner cell para
    (see ``apply_cell_chrome_and_paragraph_style``); keep this for callers
    that still want cell-direct font/size/bold/align.
    """
    props = expand_font_props(props)
    out = {}
    fam = font_family_of(props)
    if fam:
        out["font"] = fam
    for k in ("size", "bold", "italic", "align"):
        if props.get(k) not in (None, "", "none"):
            out[k] = props[k]
    return out


# 段落 props 里属于「字符级」的键：拆成多个 run 时必须显式带到每个普通 run 上，
# 否则 Word/officecli 后续 run 会落到文档默认字体，看起来「关键字前后整段格式都不一样」。
_CHAR_PROP_KEYS = (
    "font.latin", "font.ea", "font", "size", "bold", "italic", "color",
    "underline", "superscript", "subscript", "caps", "smallcaps",
)


def char_props_from(props: dict | None) -> dict:
    """抽出可挂在 run 上的字符格式（不含段落对齐/间距等）。"""
    props = expand_font_props(props)
    return {k: v for k, v in props.items()
            if k in _CHAR_PROP_KEYS and v not in (None, "", "none")}


def merge_run_props(base_char: dict, run_override: dict | None) -> dict:
    """普通正文 = base；强调 run = base + 局部覆盖（只改 color/bold 等，字体字号仍跟宿主段）。"""
    out = dict(base_char or {})
    if run_override:
        out.update({k: v for k, v in run_override.items() if v not in (None, "", "none")})
    return out


def _ensure_run_visible(base_char: dict, run_override: dict | None) -> dict | None:
    """宿主段已加粗时，仅 bold 的 run 看不出差异 → 补下划线。"""
    if run_override is None:
        return None
    out = dict(run_override)
    if not bool(base_char.get("bold")):
        return out
    has_extra = any(
        out.get(k) not in (None, "", "none", False)
        for k in ("underline", "italic", "color", "highlight", "strike")
    )
    if out.get("bold") and not has_extra:
        out["underline"] = out.get("underline") or "single"
    return out


# 这些 section 键 officecli 仅在 `set` 支持，`add --type section` 会拒绝（返回码 2）
_SET_ONLY_SECTION_KEYS = {"pgBorders"}


# officecli header/footer 支持的键（font.ea / font.latin 会 WARNING 并以非 0 退出）
_HEADER_FOOTER_KEYS = {
    "text", "field", "align", "size", "color", "bold", "italic",
    "type", "direction", "font",  # plain font 偶发可用；点分 font.* 不支持
}


def _section_hf_props(role_spec: dict, key: str, fallback_title: str,
                      *, default_type: str | None = None) -> dict:
    """合并 section 角色的 header/footer/header_first 与默认值。

    页眉文案默认用节标题；页脚默认页码域。角色若给出 text/align/size/color/field 等则覆盖。
    **显式 ``text: ""``（人改清空页眉）必须保留为空**，禁止再填回 fallback 节标题。
    过滤 officecli 页眉/页脚不支持的属性（如 font.ea），避免 add header 返回码 2 导致整次重渲失败。
    """
    raw = (role_spec or {}).get(key) or {}
    if not isinstance(raw, dict):
        raw = {}
    explicit_empty_text = "text" in raw and str(raw.get("text") or "") == ""
    if key == "footer":
        out = {"field": "page", "align": "center", "size": "9pt", "color": "#808080"}
    elif key == "header_first":
        out = {"type": "first", "text": fallback_title, "align": "center",
               "size": "9pt", "color": "#B0B0B0"}
    else:
        out = {"text": fallback_title, "align": "center", "size": "9pt", "color": "#808080"}
    for k, v in raw.items():
        if v is None or v == "none":
            continue
        # Allow explicit empty header/footer text (e.g. blank title-page header).
        if v == "" and k != "text":
            continue
        out[k] = v
    if default_type and "type" not in out:
        out["type"] = default_type
    if explicit_empty_text:
        out["text"] = ""
        out.pop("field", None)
    elif key != "footer" and not out.get("text") and not out.get("field"):
        out["text"] = fallback_title
    fam = font_family_of(out)
    if fam:
        out["font"] = fam
    # Drop unsupported / risky keys (extract 常把格内 effective.font 写进 header)
    cleaned = {}
    for k, v in out.items():
        if k.startswith("font."):
            continue
        if k == "font" and not isinstance(v, str):
            continue
        if k not in _HEADER_FOOTER_KEYS:
            continue
        cleaned[k] = v
    return cleaned


def _nonempty(d: dict) -> dict:
    return {k: v for k, v in (d or {}).items() if v not in (None, "", "none")}


def _chrome_props(d: dict) -> dict:
    """Like _nonempty, but keep border.*=none/nil so Word default grid can be cleared."""
    out: dict = {}
    for k, v in (d or {}).items():
        if v is None or v == "":
            continue
        if str(k).startswith("border."):
            v = _normalize_border_value(v)
            if str(v).lower() in ("none", "nil", "0"):
                out[k] = v
                continue
        if v == "none":
            continue
        out[k] = v
    return out


_ROMAN_PAGE = {
    "i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10,
}

# officecli pageNumFmt 合法值；模型常把「阿拉伯数字页码」写成 arabic
_PAGE_NUM_FMT_ALIASES = {
    "arabic": "decimal",
    "arab": "decimal",
    "arabicdigit": "decimal",
    "arabicdigits": "decimal",
    "number": "decimal",
    "numbers": "decimal",
    "num": "decimal",
    "1": "decimal",
    "roman": "lowerRoman",
    "romanlower": "lowerRoman",
    "romanupper": "upperRoman",
}
_PAGE_NUM_FMT_OK = {"decimal", "lowerRoman", "upperRoman", "lowerLetter", "upperLetter", "none"}

# officecli section ``type`` (break kind). LLM often writes "newPage" for "next page".
_SECTION_BREAK_OK = {"nextPage", "continuous", "evenPage", "oddPage", "nextColumn"}
_SECTION_BREAK_ALIASES = {
    "newpage": "nextPage",
    "nextpage": "nextPage",
    "next_page": "nextPage",
    "pagebreak": "nextPage",
    "page_break": "nextPage",
    "breaknewpage": "nextPage",
    "continuous": "continuous",
    "evenpage": "evenPage",
    "even_page": "evenPage",
    "oddpage": "oddPage",
    "odd_page": "oddPage",
    "nextcolumn": "nextColumn",
    "next_column": "nextColumn",
}


def _sanitize_section_break_type(value):
    """Map aliases like ``newPage`` → ``nextPage``; unknown → ``nextPage``."""
    if value is None:
        return "nextPage"
    s = str(value).strip()
    if not s or s.lower() == "none":
        return "nextPage"
    if s in _SECTION_BREAK_OK:
        return s
    for ok in _SECTION_BREAK_OK:
        if ok.lower() == s.lower():
            return ok
    key = re.sub(r"[^a-z0-9]", "", s.lower())
    return _SECTION_BREAK_ALIASES.get(key, "nextPage")


def _sanitize_page_start(value):
    """officecli pageStart 只要整数；'none'/空→省略；罗马数字 'i'→1。"""
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() == "none":
        return None
    if s.isdigit() or (s.startswith("-") and s[1:].isdigit()):
        return int(s)
    low = s.lower()
    if low in _ROMAN_PAGE:
        return _ROMAN_PAGE[low]
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return 1


def _sanitize_page_num_fmt(value):
    """Map aliases like 'arabic' → 'decimal'; drop unknown."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if s in _PAGE_NUM_FMT_OK:
        return None if s == "none" else s
    key = re.sub(r"[^a-z0-9]", "", s.lower())
    mapped = _PAGE_NUM_FMT_ALIASES.get(key)
    if mapped:
        return mapped
    # case-insensitive exact among OK
    for ok in _PAGE_NUM_FMT_OK:
        if ok.lower() == s.lower():
            return None if ok == "none" else ok
    return "decimal"


_COL_LABEL = {
    "label": "项目", "code": "编码", "value": "数值", "note": "备注", "data": "数据",
    "stub": "项目", "total": "合计", "header": "表头", "value1": "数值", "value2": "数值",
    "cell": "内容", "no": "序号", "option": "选项", "prompt": "题干", "response": "作答",
    "box": "作答栏", "summary": "摘要", "prob": "题项", "photo": "照片", "name": "姓名",
    "contact": "联系方式", "role": "角色", "definition": "定义", "excerpt": "摘录",
    # full position keys from Role libraries
    "label_col": "项目", "value_col": "内容",
    "stub_or_id_col": "样本编号", "stub_col": "行标签",
    "body_cells": "内容", "note_cells": "备注",
    "label_cells": "项目", "value_cells": "内容", "photo_cell": "照片",
    "code_col": "编码", "definition_col": "定义", "excerpt_or_note_col": "摘录",
    "header_row": "表头",
}


def _pretty_col(pos: str) -> str:
    """Map stylesheet position keys to readable Chinese column titles (not raw 'col'/'cells')."""
    key = str(pos or "").strip()
    if key in _COL_LABEL:
        return _COL_LABEL[key]
    low = key.lower()
    if low in _COL_LABEL:
        return _COL_LABEL[low]
    # strip common suffixes then map last semantic token
    base = re.sub(r"_(?:col|cols|cell|cells|row|rows)$", "", low)
    if base in _COL_LABEL:
        return _COL_LABEL[base]
    tok = base.split("_")[-1] if base else low
    if tok in _COL_LABEL:
        return _COL_LABEL[tok]
    # last resort: humanize snake_case → short Chinese-ish fallback, never dump 'cells'
    if tok in ("col", "cells", "cell", "row"):
        return "内容"
    return tok or "内容"


def _cell_class(pos: str, para_role: str = "") -> str:
    """把单元格位置归到一类样式：表头/合计/注释/行标签/数据。"""
    s = (str(pos) + " " + str(para_role or "")).lower()
    if "header" in s or "head" in s:
        return "header"
    if "total" in s or "sum" in s:
        return "total"
    if "note" in s or "source" in s or "foot" in s:
        return "note"
    if "stub" in s or "label" in s or "rowhead" in s:
        return "stub"
    return "data"


def _table_is_open(tf: dict) -> bool:
    """True when table_format intends no full grid (三线/信息表/无框)."""
    if not tf:
        return False
    if str(tf.get("border.all") or "").lower() in ("none", "nil", "0"):
        return True
    # top/bottom only without border.all → open sides
    if tf.get("border.top") or tf.get("border.bottom"):
        if not tf.get("border.all") and not tf.get("border.left") and not tf.get("border.right"):
            return True
    return False


def _finalize_cell_chrome(chrome: dict, *, table_open: bool) -> dict:
    """Word keeps default cell grid unless sides are cleared — fix open-table bleed."""
    cleaned: dict = {}
    for k, v in (chrome or {}).items():
        if v in (None, ""):
            continue
        cleaned[k] = v
    if not table_open:
        return cleaned
    border_keys = [k for k in cleaned if str(k).startswith("border.")]
    if not border_keys:
        cleaned["border.all"] = "none"
        return cleaned
    if "border.all" in cleaned and str(cleaned["border.all"]).lower() in ("none", "nil"):
        # border.all=none alone is not enough; Word still draws leftover side singles
        # (common after shared ParaTbl* chrome was wrongly merged into info tables).
        cleaned["border.all"] = "none"
        for side in ("top", "bottom", "left", "right"):
            cleaned[f"border.{side}"] = "nil"
        for k in list(cleaned):
            if re.match(r"border\.(top|bottom|left|right)\.", str(k)):
                cleaned.pop(k, None)
        return cleaned
    # underline-only (info value cell): clear other sides so Word doesn't keep grid
    if "border.bottom" in cleaned and "border.all" not in cleaned:
        for side in ("top", "left", "right"):
            cleaned[f"border.{side}"] = "nil"
        for k in list(cleaned):
            if re.match(r"border\.(top|left|right)\.", str(k)):
                cleaned.pop(k, None)
    elif "border.top" in cleaned and "border.bottom" in cleaned and "border.all" not in cleaned:
        for side in ("left", "right"):
            cleaned[f"border.{side}"] = "nil"
    return cleaned


def _repeat_header_enabled(tf: dict) -> bool | None:
    """True/False when ``table_format.repeat_header`` is set; else None (leave alone)."""
    if "repeat_header" not in (tf or {}):
        return None
    v = tf.get("repeat_header")
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("true", "1", "yes", "on"):
        return True
    if s in ("false", "0", "no", "off"):
        return False
    return None


def _header_row_indices(grid) -> list[int]:
    """1-based indices of header rows; default ``[1]`` when no cell is tagged header."""
    found: list[int] = []
    if not isinstance(grid, list):
        return [1]
    for ri, row in enumerate(grid, start=1):
        if not isinstance(row, list):
            continue
        for cell in row:
            if isinstance(cell, (list, tuple)) and cell:
                pos = str(cell[0] or "")
            elif isinstance(cell, dict):
                pos = str(cell.get("cell_style") or cell.get("pos") or "")
            else:
                continue
            pl = pos.lower()
            if "header" in pl or pl in ("head",):
                found.append(ri)
                break
    return found or [1]


def apply_table_repeat_header(
    doc: Path,
    tbl_path: str,
    tf: dict,
    grid=None,
) -> None:
    """Map ``table_format.repeat_header`` → row ``header=true|false`` (w:tblHeader).

    officecli has no writable table-level ``repeat_header``; Word stores the flag
    on header row(s). Call sites must strip ``repeat_header`` from table set_props.
    """
    enabled = _repeat_header_enabled(tf)
    if enabled is None:
        return
    for ri in _header_row_indices(grid):
        try:
            set_props(
                doc,
                f"{tbl_path}/row[{ri}]",
                {"header": "true" if enabled else "false"},
            )
        except RuntimeError as exc:
            print(f"  [warn] {tbl_path}/row[{ri}] repeat_header: {exc}")


def cell_address_paths(tbl: str, ri: int, ci: int) -> tuple[list[str], list[str]]:
    """Cell chrome paths vs inner-paragraph paths (officecli aliases)."""
    cell = [
        f"{tbl}/row[{ri}]/cell[{ci}]",
        f"/body/tbl[last()]/tr[{ri}]/tc[{ci}]",
        f"/body/table[last()]/row[{ri}]/cell[{ci}]",
    ]
    para = [
        f"{tbl}/row[{ri}]/cell[{ci}]/p[1]",
        f"/body/tbl[last()]/tr[{ri}]/tc[{ci}]/p[1]",
        f"/body/table[last()]/row[{ri}]/cell[{ci}]/p[1]",
    ]
    return cell, para


def _set_props_first_ok(doc: Path, paths: list[str], payload: dict) -> None:
    if not payload:
        return
    # Need real write feedback per path — flush queued cmds, then try immediately.
    flush_officecli_batch(doc)
    last_exc: Exception | None = None
    seen: set[str] = set()
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        try:
            set_props(doc, path, payload, force_immediate=True)
            return
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
    if last_exc:
        raise last_exc


def apply_cell_chrome_and_paragraph_style(
    doc: Path,
    *,
    tbl: str,
    ri: int,
    ci: int,
    chrome: dict,
    para_rid: str,
    text: str | None,
) -> None:
    """Tbl* chrome on the cell; Para*Cell as named ``pStyle`` on the inner paragraph.

    Font/size/bold/align are **not** written as direct cell formatting — they live
    on the named paragraph style created by ``_ensure_para_styles``.
    ``text=None`` leaves existing cell text (reapply path).
    """
    cell_paths, para_paths = cell_address_paths(tbl, ri, ci)
    if chrome:
        _set_props_first_ok(doc, cell_paths, chrome)

    para_payload: dict = {}
    if text is not None:
        para_payload["text"] = text
    if para_rid:
        para_payload["style"] = para_rid
    if not para_payload:
        return

    try:
        _set_props_first_ok(doc, para_paths, para_payload)
        return
    except Exception:
        pass

    # Writing text on the cell creates the inner paragraph; then bind pStyle.
    if text is not None:
        try:
            _set_props_first_ok(doc, cell_paths, {"text": text})
        except Exception as exc:  # noqa: BLE001
            print(f"  [warn] cell[{ri},{ci}] text: {str(exc).splitlines()[-1][:120]}")
    if para_rid:
        try:
            _set_props_first_ok(doc, para_paths, {"style": para_rid})
        except Exception as exc:  # noqa: BLE001
            print(
                f"  [warn] cell[{ri},{ci}] style={para_rid}: "
                f"{str(exc).splitlines()[-1][:120]}"
            )


def _apply_table_three_layers(
    doc: Path,
    *,
    tbl_spec: dict,
    grid: list[list[tuple[str, str, str]]],
    roles: dict,
    mark_cells: bool = True,
) -> list:
    """Render table from LLM-declared styles only.

    Each grid cell is ``(cell_style, paragraph_style, text)`` after
    :func:`assign_declared_cell_styles`. Chrome comes from ``Tbl*.cells[cell_style]``
    on the cell; inner paragraph uses named style ``paragraph_style`` (Para*Cell).
    """
    tf = table_format_of(tbl_spec)
    chrome_by_pos = llm_cells_chrome(tbl_spec)
    open_tbl = _table_is_open(tf)
    grid = assign_declared_cell_styles(grid, tbl_spec)
    nrows = len(grid)
    ncols = max((len(r) for r in grid), default=1)
    add_props(doc, "/body", typ="table", props={"rows": nrows, "cols": ncols})
    tbl = "/body/table[last()]"
    tf_set = {k: v for k, v in tf.items() if k != "repeat_header" and v not in (None, "", "none")}
    # If LLM cleared table-level borders, keep that; do not re-impose preset rules.
    if open_tbl or str(tf.get("border.all") or "").lower() in ("none", "nil", "0"):
        tf_set = {k: v for k, v in tf_set.items()
                  if k not in ("border.top", "border.bottom", "border.left", "border.right")}
        if "border.all" not in tf_set:
            tf_set["border.all"] = "none"
    if tf_set:
        try:
            set_props(doc, tbl, tf_set)
        except RuntimeError:
            soft = {k: v for k, v in tf_set.items()
                    if k in ("align", "width", "layout") or str(k).startswith("border")}
            if soft:
                set_props(doc, tbl, soft)
    apply_table_repeat_header(doc, tbl, tf, grid)

    applied_chrome: list[list[dict]] = []
    for ri, row in enumerate(grid, start=1):
        applied_row: list[dict] = []
        for ci, (cell_style, para_rid, text) in enumerate(row, start=1):
            raw_chrome = chrome_for_declared_style(chrome_by_pos, cell_style)
            chrome = _chrome_props(
                _finalize_cell_chrome(raw_chrome, table_open=open_tbl)
            )
            body = with_para_marker(para_rid, text) if mark_cells else str(text or "")
            apply_cell_chrome_and_paragraph_style(
                doc, tbl=tbl, ri=ri, ci=ci,
                chrome=chrome, para_rid=para_rid, text=body,
            )
            applied_row.append({
                "cell_style": cell_style,
                "pos": cell_style,
                "role_id": para_rid,
                "paragraph_style": para_rid,
                "chrome": dict(chrome),
            })
        applied_chrome.append(applied_row)
    return applied_chrome

def _ensure_para_styles(doc: Path, roles: dict, *, also: set[str] | None = None) -> set[str]:
    """Create Word named paragraph styles for every Para* role (seed contract).

    Returns the set of style ids we attempted to register. ``also`` may list
    extra Para* ids used by the outline but missing from ``roles`` (avoids
    officecli ``style 'X' not found in styles part``).
    """
    created: set[str] = set()
    ids = [str(r) for r in (roles or {}) if str(r).startswith("Para")]
    for rid in sorted(also or []):
        if rid.startswith("Para") and rid not in ids:
            ids.append(rid)
    for rid in ids:
        spec = (roles or {}).get(rid) if isinstance((roles or {}).get(rid), dict) else {}
        if not isinstance(spec, dict):
            spec = {}
        props = style_props_for_paragraph(spec.get("props") or {})
        try:
            add_props(
                doc, "/styles", typ="style",
                props={
                    "id": rid,
                    "name": spec.get("display_name") or rid,
                    "type": "paragraph",
                    "basedOn": "Normal",
                    **props,
                },
            )
            created.add(rid)
        except RuntimeError as exc:
            print(f"  [warn] style {rid}: {str(exc).splitlines()[-1][:120]}")
            soft_keys = (
                "font.ea", "font.latin", "font", "size", "bold", "italic",
                "align", "spaceBefore", "spaceAfter", "lineSpacing",
                "outlineLvl", "color", "firstLineIndent", "hangingIndent",
            )
            try:
                add_props(
                    doc, "/styles", typ="style",
                    props={
                        "id": rid,
                        "name": spec.get("display_name") or rid,
                        "type": "paragraph",
                        "basedOn": "Normal",
                        **{k: v for k, v in props.items() if k in soft_keys},
                    },
                )
                created.add(rid)
            except RuntimeError as exc2:
                print(f"  [warn] style {rid} soft-fail: {str(exc2).splitlines()[-1][:120]}")
    return created


def _para_roles_from_plan(plan: dict) -> set[str]:
    out: set[str] = set()
    for entry in plan.get("outline") or []:
        if not isinstance(entry, dict):
            continue
        for b in entry.get("blocks") or []:
            if not isinstance(b, dict):
                continue
            rid = str(b.get("role_id") or "")
            if rid.startswith("Para"):
                out.add(rid)
    for s in plan.get("stylesheet") or []:
        if isinstance(s, dict):
            sid = str(s.get("style_id") or "")
            if sid.startswith("Para"):
                out.add(sid)
    return out


def _table_sample_obj(
    block: dict, role: str, pos_para: dict, *, locale: str = "zh", theme: str = "",
    canonical_main_point: str = "",
) -> dict:
    """Shape expected by build_grid_for_table from a step2 outline block."""
    from LongDocFmtEval.pipeline_auto.seed.build_table_samples import (  # noqa: PLC0415
        effective_cover_main_point,
    )

    pmap = pos_para.get(role) or {}
    cells = [{"pos": pos, "role_id": pr} for pos, pr in pmap.items() if pr]
    mp = block.get("main_point") or ""
    if str(role).startswith("TblCover") or role == "TblCoverInfo":
        mp = effective_cover_main_point(mp, canonical_main_point)
    return {
        "role_id": role,
        "object_id": block.get("block_id") or role,
        "block_id": block.get("block_id") or role,
        "main_point": mp,
        "cells": cells,
        "locale": locale,
        "theme": theme or block.get("theme") or "",
    }


def _normalize_table_ref_row(row: Any) -> list[Any] | None:
    """Normalize specimen/outline ``table_ref.rows`` item to a list of cells."""
    if isinstance(row, list):
        return row
    if not isinstance(row, dict):
        return None
    cells = row.get("cells")
    if isinstance(cells, list):
        return cells
    # Parallel arrays without per-cell objects (styles only → no text)
    texts = row.get("text")
    styles = row.get("cell_style")
    paras = row.get("paragraph_style")
    if isinstance(texts, list) and texts:
        out: list[dict] = []
        for i, t in enumerate(texts):
            cell: dict[str, Any] = {"text": str(t or "")}
            if isinstance(styles, list) and i < len(styles):
                cell["cell_style"] = styles[i]
            if isinstance(paras, list) and i < len(paras):
                cell["paragraph_style"] = paras[i]
            out.append(cell)
        return out
    return None


def _cell_has_text(cell: Any) -> bool:
    if isinstance(cell, dict):
        return bool(str(cell.get("text") or "").strip())
    return bool(str(cell or "").strip())


def _row_has_text(row: list[Any]) -> bool:
    return any(_cell_has_text(c) for c in row)


def _row_looks_like_header(row: list[Any]) -> bool:
    styles: list[str] = []
    for c in row:
        if isinstance(c, dict):
            styles.append(str(c.get("cell_style") or c.get("pos") or "").lower())
        else:
            styles.append("")
    if not styles:
        return False
    headerish = {"header", "header_row", "col_header"}
    return all((s in headerish or s.startswith("header")) for s in styles if s) and any(styles)


def table_content_from_outline_block(block: dict | None) -> dict[str, Any] | None:
    """Build step6-shaped ``{header, rows}`` from specimen/outline table block.

    Preference:
      1. ``table_ref.rows`` with real cell text (same contract as content.json)
      2. pipe matrix in ``main_point`` (``A|B|C`` lines)
    """
    block = block or {}
    tr = block.get("table_ref")
    if isinstance(tr, dict):
        raw = tr.get("rows")
        if isinstance(raw, list) and raw:
            rows: list[list[Any]] = []
            for item in raw:
                norm = _normalize_table_ref_row(item)
                if norm is not None:
                    rows.append(norm)
            if rows and any(_row_has_text(r) for r in rows):
                header: list[Any] = []
                body = rows
                if _row_looks_like_header(rows[0]):
                    header = rows[0]
                    body = rows[1:]
                # Drop style-only rows that survived mixed grids
                body = [r for r in body if _row_has_text(r)]
                if header or body:
                    out: dict[str, Any] = {"rows": body}
                    if header:
                        out["header"] = header
                    return out

    matrix = parse_pipe_matrix(str(block.get("main_point") or ""))
    if matrix:
        # 2-column pipe lines are label|value rows (cover / KV), not header+body.
        if len(matrix[0]) == 2:
            return {"rows": matrix}
        return {"header": matrix[0], "rows": matrix[1:]}
    return None


def build_table(doc: Path, role: str, spec: dict, roles: dict, pos_para: dict,
                *, block: dict | None = None, include_note_row: bool = False,
                mark_cells: bool = True, locale: str = "zh", theme: str = "",
                canonical_main_point: str = "") -> list:
    """Render one table with distinctive per-row content (seed build_table_samples).

    Does NOT reuse the same ParaTbl*.example for every data row.
    Cell text carries ``{ParaTbl*}`` markers when ``mark_cells``; chrome from Tbl* cells.
    Returns the rendered grid (list of rows of (pos, para_rid, text)).
    """
    del include_note_row  # samples own their rows; note rows come from Para* blocks in outline
    block = block or {}
    obj = _table_sample_obj(
        block, role, pos_para, locale=locale, theme=theme,
        canonical_main_point=canonical_main_point,
    )
    if not obj["cells"]:
        # fallback: invent positions from role props.cells keys
        props = spec.get("props", {}) if isinstance(spec, dict) else {}
        cell_keys = list((props.get("cells") or {}).keys())
        obj["cells"] = [{"pos": k, "role_id": f"ParaTbl{k.title()}"} for k in cell_keys]
    sample = build_grid_for_table(obj)
    grid_dicts = sample.get("grid") or []
    if not grid_dicts:
        return []
    grid: list[list[dict]] = []
    for row in grid_dicts:
        grid.append([
            {
                "pos": str(c.get("pos") or "cell"),
                "cell_style": c.get("cell_style"),  # optional explicit
                "role_id": str(c.get("role_id") or "ParaTblData"),
                "paragraph_style": str(c.get("role_id") or "ParaTblData"),
                "text": str(c.get("text") or ""),
            }
            for c in row
        ])
    tbl_spec = roles.get(role) or spec or {}
    if "props" in tbl_spec and "table_format" not in tbl_spec and "cells" not in tbl_spec:
        pass
    _apply_table_three_layers(
        doc, tbl_spec=tbl_spec, grid=grid, roles=roles, mark_cells=mark_cells,
    )
    declared = assign_declared_cell_styles(grid, tbl_spec)
    return [
        [
            {
                "pos": cs,
                "cell_style": cs,
                "role_id": para,
                "paragraph_style": para,
                "text": txt,
            }
            for cs, para, txt in row
        ]
        for row in declared
    ]


def build_table_content(doc: Path, role: str, spec: dict, roles: dict, pos_para: dict,
                        content: dict, *, mark_cells: bool = False,
                        warnings_out: list | None = None,
                        block_id: str = "") -> list[list[dict]]:
    """Gold: table text from content; chrome/paragraph styles from LLM declarations.

    Preferred cell shape::
        {"text": "...", "cell_style": "header|data|label|…", "paragraph_style": "Para…Cell"}
    Plain strings: same physical slots as step4 samples (LLM ``cells`` /
    ``cell_style_plan`` / position_map). Extra year/metric columns clone ``data``.

    Returns grid dicts with resolved ``cell_style`` / ``paragraph_style`` for tables.json.
    Does **not** silently rewrite bad paragraph_style; appends to ``warnings_out`` instead.
    """
    from LongDocFmtEval.pipeline_auto.style_taxonomy import (  # noqa: PLC0415
        is_table_paragraph_object,
        looks_like_table_paragraph_id,
    )

    pmap = pos_para.get(role, {}) or {}
    raw_header = content.get("header") or []
    raw_rows = content.get("rows") or []
    tbl_spec = roles.get(role) or spec or {}
    plan = cell_style_plan_of(tbl_spec)
    allowed_paras = {
        str(v).strip()
        for v in (pmap.values() if isinstance(pmap, dict) else [])
        if str(v or "").strip()
    }
    for rid, rspec in (roles or {}).items():
        if is_table_paragraph_object((rspec or {}).get("object"), rid) or looks_like_table_paragraph_id(rid):
            allowed_paras.add(str(rid))

    def _parse_cell(c, *, default_pos: str, default_para: str) -> dict:
        if isinstance(c, dict):
            para = c.get("paragraph_style") or c.get("role_id") or default_para
            return {
                "text": str(c.get("text") or ""),
                "cell_style": c.get("cell_style"),
                "paragraph_style": para,
                "pos": c.get("pos") or default_pos,
            }
        return {
            "text": str(c or ""),
            "cell_style": None,
            "paragraph_style": default_para,
            "pos": default_pos,
        }

    def _para_of(pos: str) -> str:
        return str(pmap.get(pos) or ("ParaTblHeaderCell" if pos == "header" else "ParaTblDataCell"))

    def _note_bad_para(ri: int, ci: int, cell: dict) -> None:
        if warnings_out is None:
            return
        para = str(cell.get("paragraph_style") or "").strip()
        if not para:
            return
        if para in allowed_paras or looks_like_table_paragraph_id(para):
            return
        # Body / other non-cell paragraph used inside a table cell
        suggest = _para_of(str(cell.get("pos") or cell.get("cell_style") or "data"))
        warnings_out.append({
            "block_id": block_id or "",
            "table_role": role,
            "row": ri + 1,
            "col": ci + 1,
            "cell_style": cell.get("cell_style") or cell.get("pos") or "",
            "paragraph_style": para,
            "suggested_paragraph_style": suggest,
            "reason": (
                "table-paragraph must be Para*Cell (object=paragraph.table_cell); "
                f"got body/other style {para!r}"
            ),
        })

    is_label_value = (
        any(k in pmap for k in ("label", "label_col", "label_cells", "key"))
        and any(k in pmap for k in ("value", "value_col", "value_cells"))
        and "header" not in pmap
        and "header_row" not in pmap
    )

    def _width(row) -> int:
        if isinstance(row, list):
            return len(row)
        return 0

    ncols = max([_width(raw_header)] + [_width(r) for r in raw_rows] + [1])
    body_slots = physical_body_slots(pmap, ncols, plan=plan)

    def _body_slot(ci: int) -> tuple[str, str]:
        if ci < len(body_slots):
            return body_slots[ci]
        if body_slots:
            return body_slots[-1]
        return ("data", "ParaTblDataCell")

    def _pad_row(cells: list) -> list:
        out = list(cells)
        while len(out) < ncols:
            out.append("")
        return out[:ncols]

    grid: list[list[dict]] = []
    if raw_header and not is_label_value:
        grid.append([
            _parse_cell(c, default_pos="header", default_para=_para_of("header"))
            for c in _pad_row(list(raw_header))
        ])
    for row in raw_rows:
        padded = _pad_row(list(row) if isinstance(row, list) else [])
        line: list[dict] = []
        for ci, c in enumerate(padded):
            pos, para = _body_slot(ci)
            cell = _parse_cell(c, default_pos=pos, default_para=para or _para_of(pos))
            if not cell.get("cell_style"):
                cell["cell_style"] = None
            line.append(cell)
        grid.append(line)

    if not grid:
        return []

    for ri, row in enumerate(grid):
        for ci, cell in enumerate(row):
            _note_bad_para(ri, ci, cell)

    tup_grid = []
    for row in grid:
        tup_grid.append([
            {
                "text": c["text"],
                "cell_style": c.get("cell_style"),
                "paragraph_style": c.get("paragraph_style"),
                "pos": c.get("pos") or "data",
                "role_id": c.get("paragraph_style"),
            }
            for c in row
        ])
    _apply_table_three_layers(
        doc, tbl_spec=tbl_spec, grid=tup_grid, roles=roles, mark_cells=mark_cells,
    )
    declared = assign_declared_cell_styles(tup_grid, tbl_spec)
    return [
        [
            {
                "pos": cs,
                "cell_style": cs,
                "role_id": para,
                "paragraph_style": para,
                "text": txt,
            }
            for cs, para, txt in row
        ]
        for row in declared
    ]


def write_table_cell_warning_md(path: Path, warnings: list[dict], *, locale: str = "zh") -> None:
    """Human-readable report when table cells use non-table-paragraph styles."""
    path = Path(path)
    if not warnings:
        if path.is_file():
            path.unlink(missing_ok=True)
        return
    en = str(locale).lower().startswith("en")
    lines = [
        "# table-cell-warning" if en else "# 表格格内段落样式告警",
        "",
        (
            "Cells used a `paragraph_style` that is not **table-paragraph** "
            "(`paragraph.table_cell` / Para*Cell). Rendering kept the LLM declaration; "
            "please fix content or re-run step6."
            if en else
            "表格单元格的 `paragraph_style` 不是 **table-paragraph**"
            "（`paragraph.table_cell` / Para*Cell）。渲染仍按模型声明写入，未静默改写；"
            "请改 content 或重跑 step6。"
        ),
        "",
        (
            "| block | table | row | col | cell_style | bad paragraph_style | suggested |"
            if en else
            "| block | 表 | 行 | 列 | cell_style | 错误 paragraph_style | 建议 |"
        ),
        "|---|---|---:|---:|---|---|---|",
    ]
    for w in warnings:
        lines.append(
            f"| {w.get('block_id','')} | {w.get('table_role','')} | "
            f"{w.get('row','')} | {w.get('col','')} | {w.get('cell_style','')} | "
            f"`{w.get('paragraph_style','')}` | `{w.get('suggested_paragraph_style','')}` |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _heading_text(
    block: dict, role_props: dict, role_id: str, example: str, *, prefer_example: bool = False,
) -> str:
    """Pick display text for a block.

    Prefer this block's ``main_point`` (so repeated uses of the same style show distinct
    cases). True chapter/section headings may unwrap a quoted title when main_point is
    instructional (e.g. ``本章论述「发展基础」``). Table/figure captions and other
    *Title* roles keep the full main_point — do not strip mid-phrase quotes like
    ``表1 “十四五”时期…`` down to just ``十四五``.
    Fall back to the role's shared ``example`` when main_point is empty.
    For annotated template body (``prefer_example``), use the role specimen when it is
    clearly longer than the planning stub so the preview can show the style.
    """
    mp = (block.get("main_point") or "").strip()
    rid = role_id or ""
    # Captions / cover / TOC titles: main_point IS the display line.
    is_captionish = bool(
        re.search(
            r"(TableTitle|FigureTitle|FigTitle|Caption|CoverTitle|TocTitle|表题|图题)",
            rid,
            re.I,
        )
    )
    is_structural_head = (
        ("outlineLvl" in (role_props or {}))
        or ("Heading" in rid)
    ) and not is_captionish
    if is_structural_head and mp:
        m = re.search(r'[“"「『]([^”"」』]{1,50})[”"」』]', mp)
        if m:
            quoted = m.group(1).strip()
            rest = (mp[: m.start()] + mp[m.end() :]).strip()
            # Only unwrap when quotes hold the bulk of the title (wrapper prose outside).
            # Mid-phrase emphasis (短引号 + 两侧仍有实质文字) → keep full mp.
            if quoted and len(rest) <= max(12, len(quoted) // 4):
                return quoted
        m2 = re.match(r'^[^：:]{0,12}[：:]\s*(.+)$', mp)
        if m2 and len(m2.group(1)) <= 40:
            return m2.group(1).strip()
    is_body = (not is_structural_head) and (not is_captionish)
    ex = (example or "").strip()
    if prefer_example and is_body and ex:
        if (not mp) or len(ex) >= max(40, len(mp) + 8):
            return ex if len(ex) <= 400 else (ex[:397] + "…")
    if mp:
        # Body / list / caption paras need full case text; only trim extremely long blobs.
        return mp if len(mp) <= 240 else (mp[:237] + "…")
    return ex[:240] if ex else ""


# Manual list markers that duplicate Word auto-number / bullet when listStyle is set.
_MANUAL_LIST_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"(?:[0-9]{1,3}|[一二三四五六七八九十百千]+)[.、．]\s*"  # 1. / 1、 / 一、
    r"|[（(](?:[0-9]{1,3}|[一二三四五六七八九十]+)[）)]\s*"  # （1） / (1)
    r"|[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮]\s*"
    r"|[-–—•●·▪▫]\s*"  # bullet-like
    r")"
)

# LLM sometimes wraps run leads in Markdown/HTML; strip before Word insert.
_LEAD_LABEL_RE = re.compile(r"^([^\n。；;！!？?]{1,24}[：:])")
_MID_LIST_SEP_RE = re.compile(
    r"(?<=[。；;！!？?])\s*[•●·▪▫]\s+"
    r"|(?<=[。；;！!？?])\s+(?=(?:[0-9]{1,3}|[一二三四五六七八九十]+)[.、．）)]\s)"
    r"|(?<=[。；;！!？?])\s+(?=[（(](?:[0-9]{1,3}|[一二三四五六七八九十]+)[）)]\s)"
)


def strip_inline_markup(text: str) -> str:
    """Remove common LLM bold wrappers (``**…**``, ``<b>…</b>``) before insert."""
    s = str(text or "")
    for _ in range(4):
        ns = re.sub(r"\*\*(.+?)\*\*", r"\1", s, flags=re.DOTALL)
        ns = re.sub(r"<b>(.*?)</b>", r"\1", ns, flags=re.IGNORECASE | re.DOTALL)
        ns = re.sub(r"<strong>(.*?)</strong>", r"\1", ns, flags=re.IGNORECASE | re.DOTALL)
        if ns == s:
            break
        s = ns
    s = s.replace("**", "")
    s = re.sub(r"</?(?:b|strong)\s*>", "", s, flags=re.IGNORECASE)
    return s


def strip_manual_list_prefix(text: str) -> tuple[str, str]:
    """Remove a leading manual ``1. `` / ``（1）`` / bullet if present.

    Returns ``(body, removed_prefix)``. Safe no-op when no prefix.
    """
    s = str(text or "")
    m = _MANUAL_LIST_PREFIX_RE.match(s)
    if not m:
        return s, ""
    return s[m.end():].lstrip(), m.group(0)


def _adjust_runs_after_prefix_strip(expected: list[dict], removed: str) -> None:
    """Drop numbering from text_before when it was only the stripped list marker."""
    if not removed:
        return
    rem = removed.strip()
    for er in expected:
        before = er.get("text_before") or ""
        if not before:
            continue
        b = before.strip()
        if b == rem or b.rstrip() == rem.rstrip() or rem.startswith(b) or b.startswith(rem.strip()):
            er["text_before"] = ""
            continue
        # e.g. text_before="1. " already gone from body — clear if body no longer has it
        if _MANUAL_LIST_PREFIX_RE.match(before):
            er["text_before"] = _MANUAL_LIST_PREFIX_RE.sub("", before, count=1)


def _style_rid_for_block(block: dict, roles: dict) -> str:
    """Resolve the stylesheet role_id used for props (not symbolic T01/F01 refs)."""
    rid = block.get("role_id") or ""
    if rid and rid in roles:
        return rid
    for key in ("table_ref", "image_ref"):
        ref = block.get(key)
        if ref and ref in roles:
            return ref
    return rid


def _split_list_item_chunks(raw: str) -> list[str]:
    """One Word list paragraph per item (``\\n`` / mid-text bullets / numbered leads)."""
    parts = [p.strip() for p in re.split(r"\n+", raw) if p.strip()]
    if len(parts) <= 1:
        one = parts[0] if parts else raw
        mid = [p.strip() for p in _MID_LIST_SEP_RE.split(one) if p and p.strip()]
        if len(mid) > 1:
            parts = mid
        else:
            # Collapsed "a. • b. • c." with no sentence-end before bullets
            alt = [p.strip() for p in re.split(r"\s*[•●·▪▫]\s+", one) if p.strip()]
            if len(alt) > 1:
                parts = alt
    out: list[str] = []
    for p in parts:
        body, _ = strip_manual_list_prefix(p)
        body = body.strip()
        if body:
            out.append(body)
    return out


def _split_gold_text_chunks(text: str, *, list_style: str | None = None) -> list[str]:
    """Split gold body into paragraphs.

    - Normal body: blank lines → separate paras (single ``\\n`` stays soft).
    - ``listStyle`` bullet/ordered: each item → its own Word list paragraph
      (do **not** collapse item newlines into one para).
    """
    raw = strip_inline_markup(str(text or "")).replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return []
    if list_style in ("ordered", "bullet"):
        return _split_list_item_chunks(raw)
    parts = re.split(r"\n[ \t]*\n+", raw)
    out = [re.sub(r"[ \t]*\n[ \t]*", "\n", p).strip() for p in parts]
    return [p for p in out if p]


def _normalize_expected_runs(raw) -> list[dict]:
    """Normalize expected_runs to [{role, text, text_before?, text_after?, reason?}].

    Accepts legacy string role ids, or objects with precise inline placement.
    Strips LLM ``**`` / ``<b>`` wrappers from run substrings.
    """
    out: list[dict] = []
    for item in (raw or []):
        if isinstance(item, str) and item.strip():
            out.append({"role": item.strip(), "text": "", "reason": ""})
        elif isinstance(item, dict):
            role = str(item.get("role") or item.get("role_id") or item.get("span_role") or "").strip()
            text = strip_inline_markup(str(item.get("text") or item.get("substr") or "")).strip()
            if not role:
                continue
            out.append({
                "role": role,
                "text": text,
                "text_before": strip_inline_markup(
                    str(item.get("text_before") or "")
                ).strip(),
                "text_after": strip_inline_markup(
                    str(item.get("text_after") or "")
                ).strip(),
                "reason": str(item.get("reason") or item.get("criterion") or "").strip(),
            })
    return out


def _find_lead_bold_role(roles: dict) -> str | None:
    """Prefer Run*Lead*Bold / bold-only lead roles for ``标签：`` auto-mark."""
    prefer: list[str] = []
    bold_only: list[str] = []
    for rid, spec in (roles or {}).items():
        if not str(rid).startswith("Run"):
            continue
        props = (spec or {}).get("props") or {}
        if not props.get("bold"):
            continue
        if props.get("superscript") or props.get("subscript"):
            continue
        rid_s = str(rid)
        if "Lead" in rid_s or rid_s.endswith("Bold") or "Bold" in rid_s:
            prefer.append(rid_s)
        elif not props.get("italic") and not props.get("underline"):
            bold_only.append(rid_s)
    if prefer:
        prefer.sort(key=lambda r: (0 if "Lead" in r else 1, len(r)))
        return prefer[0]
    return bold_only[0] if bold_only else None


def _maybe_add_lead_bold_run(expected: list[dict], text: str, roles: dict) -> list[dict]:
    """If paragraph starts with ``标签：``, ensure a bold-lead run covers it."""
    lead_role = _find_lead_bold_role(roles)
    if not lead_role:
        return expected
    m = _LEAD_LABEL_RE.match(str(text or "").strip())
    if not m:
        return expected
    label = m.group(1)
    out = list(expected or [])
    for er in out:
        frag = str(er.get("text") or "")
        if frag and (frag == label or frag.endswith(("：", ":")) and frag in text):
            return out  # already covered
        role = str(er.get("role") or "")
        if role == lead_role and frag and frag in text:
            return out
    out.append({"role": lead_role, "text": label, "text_before": "", "text_after": "", "reason": ""})
    return out


# 已知 run 角色 → 区分标准（库/旧样例缺 discriminators 时的兜底，便于预览可读）
_RUN_CRITERION_FALLBACK = {
    "RunBlankFill": "标准·填空位：待填写空白用下划线标出",
    "RunCitationMark": "标准·引用角标：引用序号/脚注标记用上标",
    "RunPersonName": "标准·专名换字体：人名等用楷体相对正文区分",
    "RunLeadKeyword": "标准·段首要词加粗：「标签：」或序号中心句领词加粗",
    "RunInterviewSpeaker": "标准·段首要词加粗：访谈「受访者X：」说话人标签加粗",
    "RunThemeCode": "标准·分析编码词：主题/编码概念加粗并下划线，勿仅装饰换色",
    "RunEmphasisCode": "标准·分析编码词：主题/编码概念应可识别（宜加粗或下划线；勿仅装饰换色）",
    "RunSourceTag": "标准·来源引导：「来源：」等题注引导词斜体+下划线",
}


def _run_criterion_legend(expected_runs: list[dict], roles: dict) -> str:
    """拼一段附在 case 文后的「run 区分标准」说明（灰色小字）。"""
    parts: list[str] = []
    seen: set[str] = set()
    for er in expected_runs:
        role = er.get("role") or ""
        if not role or role in seen:
            continue
        seen.add(role)
        reason = (er.get("reason") or "").strip()
        spec = roles.get(role) or {}
        name = spec.get("display_name") or role
        desc = spec.get("description") or ""
        if isinstance(desc, list):
            desc = "；".join(str(x) for x in desc if x)
        desc = str(desc).strip()
        if reason:
            label = reason
        elif desc.startswith("标准") or "标准·" in desc or "标准:" in desc:
            label = desc
        else:
            label = _RUN_CRITERION_FALLBACK.get(role) or (desc if desc else f"{name}（行内语义标记）")
        # 压缩过长说明
        if len(label) > 48:
            label = label[:46] + "…"
        parts.append(f"{name}→{label}" if not label.startswith(name) else label)
    if not parts:
        return ""
    return " 〔run标准｜" + "；".join(parts) + "〕"


def _run_inline_spans(base: str, expected_runs: list[dict], roles: dict,
                      run_props_fn) -> list[tuple[str, dict | None]]:
    """Apply run formatting *inside* the paragraph text (no trailing 〔run示意〕).

    Placement priority:
    1. text_before + text + text_after window already in base → mark frag in place;
    2. ``text`` (frag) already appears in base → format that span in place;
    3. before/after partial windows;
    4. **never splice** a copy of frag into a paragraph that already contains it
       (that caused duplicated bold leads like 「原则…，原则…」).
    """
    if not expected_runs:
        return [(base, None)]

    text = base
    marks: list[tuple[int, int, str]] = []  # half-open [start, end), role

    def add_mark(start: int, end: int, role: str) -> None:
        if start < 0 or end <= start or end > len(text):
            return
        for a, b, _ in marks:
            if not (end <= a or start >= b):
                return
        marks.append((start, end, role))

    for er in expected_runs:
        role = er.get("role") or ""
        frag = er.get("text") or ""
        before = er.get("text_before") or ""
        after = er.get("text_after") or ""
        if not role:
            continue

        placed = False
        if frag and (before or after):
            window = f"{before}{frag}{after}"
            idx = text.find(window) if window else -1
            if idx >= 0:
                add_mark(idx + len(before), idx + len(before) + len(frag), role)
                placed = True
            elif before and after:
                # looser: before+frag or frag+after
                w1 = f"{before}{frag}"
                i1 = text.find(w1)
                if i1 >= 0:
                    add_mark(i1 + len(before), i1 + len(before) + len(frag), role)
                    placed = True
                else:
                    w2 = f"{frag}{after}"
                    i2 = text.find(w2)
                    if i2 >= 0:
                        add_mark(i2, i2 + len(frag), role)
                        placed = True

        if not placed and frag:
            idx = text.find(frag)
            if idx >= 0:
                add_mark(idx, idx + len(frag), role)
                placed = True
            else:
                ex = str((roles.get(role) or {}).get("example") or "")
                if ex and ex in text:
                    idx = text.find(ex)
                    add_mark(idx, idx + len(ex), role)
                    placed = True

        # Last resort: only insert when the fragment is truly absent (demo gap).
        # Do NOT insert when frag is already in the paragraph (avoids duplication).
        if not placed and frag and frag not in text:
            insert_at = len(text)
            piece = frag
            text = text[:insert_at] + piece + text[insert_at:]
            add_mark(insert_at, insert_at + len(frag), role)

    if not marks:
        return [(text, None)]

    marks.sort(key=lambda m: m[0])
    spans: list[tuple[str, dict | None]] = []
    pos = 0
    for a, b, role in marks:
        if a > pos:
            spans.append((text[pos:a], None))
        spans.append((text[a:b], run_props_fn(role) or {"bold": True}))
        pos = b
    if pos < len(text):
        spans.append((text[pos:], None))
    return spans or [(text, None)]


def render(template_dir: Path, *, content: dict | None = None, annotated: bool = True,
           out_name: str = "preview.docx", assets_name: str = "assets.json",
           specimen: bool = False) -> Path:
    """把 step3 的 T* 按 step2 大纲渲染成 .docx。

    与 seed 契约对齐：
      - 段落：创建 Word 命名样式（style=role_id），正文带 ``{ParaXxx}`` 前缀；
      - 表格：按大纲 main_point 生成区分性网格（非整表复用同一 ParaTbl.example）。
    annotated 仅影响是否附加轻量 run 示意；不再使用「角色名。如：」直写格式。

    specimen=True 时才按 stylesheet 抽样（每种 role ≤1）；**默认 False**：正文按完整大纲。
    preview（annotated 且无 content）下：**仅表/图**按 ``template_exhibit``（**每种** Tbl*/Img* role ≤2 次示范；
    style 种数不限）并跳过其题注；**section / 正文 paragraph / run 一律按完整大纲落位**，不做种数/次数硬裁。
    gold（有 content）仍落全部表/图。

    **默认渲染护栏**（``apply_render_prop_guards`` / ``coerce_officecli_prop_keys``）：
      ``run_pipeline_auto`` / ``run_pipeline_overlapped`` / step5·6 / ``rerender_docx_only``
      均经本函数写 Word，因此共用：
      - margin* 裸数字 → cm
      - lineSpacing 裸数字 <3 → x；>10 → pt；3–10 不动
    """
    content = content or {}
    from LongDocFmtEval.pipeline_auto.auto_paths import (  # noqa: PLC0415
        resolve_asset_file,
        resolve_outline_sidecar,
        resolve_work_file,
    )
    tpl_path = resolve_work_file(template_dir, "template.json")
    tpl = load_json(tpl_path) if tpl_path.is_file() else {}
    from LongDocFmtEval.pipeline_auto.seed.seed_ir import load_merged_roles  # noqa: PLC0415
    roles = load_merged_roles(template_dir, tpl)
    slug = template_dir.name
    plan_path = resolve_outline_sidecar(template_dir, "plan.json")
    lib_path = resolve_outline_sidecar(template_dir, "selected_library.yaml")
    plan = load_json(plan_path) if plan_path.exists() else {}
    if plan:
        prune_notes = prune_empty_sections(plan)
        lib = {}
        if lib_path.exists():
            try:
                lib = yaml.safe_load(lib_path.read_text(encoding="utf-8")) or {}
            except Exception:  # noqa: BLE001
                lib = {}
        prune_notes.extend(strip_abstract_roles(plan, lib))
        prune_notes.extend(drop_section_role_blocks(plan, roles))
        page_notes = apply_section_page_continuity(plan)
        if page_notes:
            n_none = sum(1 for n in page_notes if "none" in n)
            n_one = sum(1 for n in page_notes if "pageStart=1" in n)
            print(f"[page] 节页码续接：重启 {n_one} 节，续前 {n_none} 节")
        if prune_notes or page_notes:
            dump_json(plan_path, plan)
            for msg in prune_notes:
                print(f"[fix] {msg}")
    locale = (
        (plan.get("meta") or {}).get("locale")
        or (tpl.get("meta") or {}).get("locale")
        or ("en" if re.search(r"(?:^|_)en(?:_|$)", slug.lower()) else "zh")
    )
    locale = "en" if str(locale).lower().startswith("en") else "zh"
    theme = str(
        (plan.get("meta") or {}).get("theme")
        or (tpl.get("meta") or {}).get("theme")
        or ""
    ).strip()
    sections = plan.get("sections") or []
    outline_map = {o.get("instance_id"): (o.get("blocks") or []) for o in (plan.get("outline") or [])}
    canonical_main_points: dict[str, str] = {}
    for _blocks in outline_map.values():
        for _b in _blocks or []:
            _bid = str((_b or {}).get("block_id") or "")
            if _bid:
                canonical_main_points[_bid] = str((_b or {}).get("main_point") or "")
    using_specimen = False
    specimen_path = Path(template_dir) / "specimen_plan.json"
    if annotated and not content and not specimen and specimen_path.is_file():
        try:
            spec = load_json(specimen_path)
        except Exception:  # noqa: BLE001
            spec = {}
        if isinstance(spec, dict) and spec.get("outline"):
            plan = {
                **plan,
                "sections": spec.get("sections") or sections,
                "outline": spec.get("outline") or [],
            }
            apply_section_page_continuity(plan)
            drop_section_role_blocks(plan, roles)
            sections = plan.get("sections") or []
            outline_map = {
                o.get("instance_id"): (o.get("blocks") or [])
                for o in (plan.get("outline") or [])
            }
            using_specimen = True
            n_blk = sum(len(v) for v in outline_map.values())
            print(
                f"[specimen] 使用 specimen_plan.json："
                f"{len(sections)} 节 / {n_blk} 块"
                f"（覆盖缺 {len((spec.get('coverage') or {}).get('missing') or [])}）"
            )
    if specimen and not content:
        from LongDocFmtEval.pipeline_auto.seed.specimen_preview import (  # noqa: PLC0415
            build_specimen_outline_map,
        )
        sections, outline_map, spec_stats = build_specimen_outline_map(plan, roles, per_role=1)
        print(
            f"[specimen] optional gallery blocks {spec_stats.get('blocks_in')}→{spec_stats.get('blocks_out')} "
            f"sections {spec_stats.get('sections_in')}→{spec_stats.get('sections_out')}"
        )
        try:
            dump_json(Path(template_dir) / "specimen_stats.json", spec_stats)
        except Exception:  # noqa: BLE001
            pass
    # preview/template：限制表/图示范次数；gold（有 content）/specimen 绝不裁
    # 注意：preview 图少 ≠ document 图少；后者按大纲全量插图
    exhibit_plan = None
    if annotated and not content and not specimen and not using_specimen:
        exhibit_plan = plan_template_exhibits(sections, outline_map, roles, per_style=2)
        print(
            f"[exhibit] preview 每种 Tbl*/Img* style 最多 {exhibit_plan.get('per_style')} 张实例"
            f"（不限制 style 种数）；"
            f"tbl_styles={exhibit_plan.get('allowed_table_styles')} "
            f"img_styles={exhibit_plan.get('allowed_image_styles')} "
            f"skip_blocks={len(exhibit_plan.get('skip_block_ids') or [])}"
        )
        try:
            dump_json(Path(template_dir) / "exhibit_stats.json", {
                "counts": exhibit_plan.get("counts"),
                "per_style": exhibit_plan.get("per_style"),
                "max_styles": exhibit_plan.get("max_styles"),
                "allowed_table_styles": exhibit_plan.get("allowed_table_styles"),
                "allowed_image_styles": exhibit_plan.get("allowed_image_styles"),
                "exhibit_block_ids": sorted(exhibit_plan.get("exhibit_block_ids") or []),
                "skip_n": len(exhibit_plan.get("skip_block_ids") or []),
            })
        except Exception:  # noqa: BLE001
            pass
    assets_path = template_dir / assets_name
    assets = load_json(assets_path) if assets_path.exists() else {}
    from LongDocFmtEval.pipeline_auto.scripts.build_figures import assign_figure_ids  # noqa: PLC0415
    fid_by_block = assign_figure_ids(plan, roles)
    assets_by_block = {
        str(v.get("block_id")): v
        for v in assets.values()
        if isinstance(v, dict) and v.get("block_id")
    }
    render_overrides: dict = {}
    gold_mode = bool(content) and not annotated
    if gold_mode or (annotated and not content):
        from LongDocFmtEval.pipeline_auto.seed.render_overrides import (  # noqa: PLC0415
            load_render_overrides,
        )
        render_overrides = load_render_overrides(template_dir)
        if render_overrides:
            note = str(render_overrides.get("note") or "").strip()
            extra = f" ({note})" if note else ""
            print(f"[override] render_overrides.json 生效{extra}")
    _fig_seen: set[str] = set()
    _fig_k = [0]

    def next_fid(b: dict) -> str:
        # Number ALL image blocks (same as collect_figures), not only the ones we emit.
        bid = str(b.get("block_id") or "")
        if bid and bid in fid_by_block:
            fid = fid_by_block[bid]
            _fig_seen.add(fid)
            return fid
        _fig_k[0] += 1
        fid = str(b.get("figure_id") or f"F{_fig_k[0]}")
        while fid in _fig_seen:
            fid = f"{fid}_{_fig_k[0]}"
        _fig_seen.add(fid)
        return fid

    def asset_info(b: dict, fid: str) -> dict:
        info = assets.get(fid) if isinstance(assets.get(fid), dict) else {}
        if info.get("asset"):
            return info
        bid = str(b.get("block_id") or "")
        fallback = assets_by_block.get(bid) if bid else None
        return fallback if isinstance(fallback, dict) else info or {}

    pos_para: dict[str, dict[str, str]] = {}
    if lib_path.exists():
        lib = yaml.safe_load(lib_path.read_text(encoding="utf-8"))
        for t in (lib.get("roles", {}).get("table") or []):
            tl = t.get("table_layout") or {}
            pos_para[t.get("role_id")] = {pos: (m or {}).get("paragraph")
                                          for pos, m in (tl.get("position_map") or {}).items()}

    def rspec(rid: str) -> dict:
        return roles.get(rid, {}) or {}

    def rprops(rid: str) -> dict:
        """Role props with officecli render guards (margin cm, lineSpacing x/pt, …)."""
        return apply_render_prop_guards(rspec(rid).get("props", {}) or {})

    def rexample(rid: str) -> str:
        return rspec(rid).get("example", "") or ""

    def run_props(run_role: str) -> dict:
        return char_props_from(rprops(run_role))

    doc = template_dir / out_name
    create_docx(doc)
    # Buffer officecli add/set into ``officecli batch`` chunks (OFFICECLI_BATCH=0 to disable).
    _ocli_cm = officecli_batch_session(doc)
    ocli_batch = _ocli_cm.__enter__()
    _batch_closed = False

    def _close_batch(exc_info=None) -> None:
        nonlocal _batch_closed
        if _batch_closed:
            return
        _batch_closed = True
        print(
            f"[officecli] render {ocli_batch.mode}: "
            f"cmds≈{ocli_batch.n_enqueued} batches={ocli_batch.n_batches} "
            f"chunk={ocli_batch.chunk_size}"
        )
        info = exc_info if exc_info is not None else (None, None, None)
        _ocli_cm.__exit__(*info)

    try:
        para_styles_ready: set[str] = set()
        try:
            para_styles_ready = _ensure_para_styles(
                doc, roles, also=_para_roles_from_plan(plan),
            )
        except Exception as e:  # noqa: BLE001
            print(f"  [warn] 创建命名样式失败: {e}")

        # Live map/styles (seed-compatible) — refreshed while emitting document body
        prior_map = load_json(template_dir / "map.json") if (template_dir / "map.json").exists() else {}
        prior_styles = load_json(template_dir / "styles.json") if (template_dir / "styles.json").exists() else {}
        new_map: dict = {
            "section": {k: list(v) for k, v in (prior_map.get("section") or {}).items()},
            "paragraph": {},
            "table": {},
            # legacy flat mirrors (polish / extract still accept these)
            "table_structure": {},
            "table_cell": {},
            "image": {},
            "runs": dict(prior_map.get("runs") or {}),
            "paragraph_objects": {},
        }
        new_styles: dict = {
            "section": dict(prior_styles.get("section") or {}),
            "paragraph": {},
            # Nested: one table style → structure locator + cell-style locators (no border/fill)
            "table": {},
            "table_structure": {},
            "table_cell": {},
            "image": {},
            "run": dict(prior_styles.get("run") or {}),
        }
        for si, sec in enumerate(sections, start=1):
            srid = sec.get("role_id") or f"Sec{si}"
            new_styles["section"].setdefault(srid, si)
            new_map["section"].setdefault(srid, [])
            if si not in new_map["section"][srid]:
                new_map["section"][srid].append(si)

        para_i = [0]
        tbl_i = [0]
        img_i = [0]
        rendered_tables: dict[str, Any] = {}
        table_cell_warnings: list[dict] = []

        # Numbering: allocate a fresh ordered list id whenever a contiguous ordered run breaks,
        # so later recommendation lists restart at 1 instead of continuing from earlier steps.
        _next_num_id = [910]
        _cur_ordered_id: list[int | None] = [None]
        _in_ordered = [False]

        def ensure_ordered_num() -> int:
            if not _in_ordered[0] or _cur_ordered_id[0] is None:
                nid = _next_num_id[0]
                _next_num_id[0] += 1
                add_props(doc, "/numbering", typ="num",
                          props={"id": nid, "format": "decimal", "text": "%1.", "start": 1})
                _cur_ordered_id[0] = nid
                _in_ordered[0] = True
            return int(_cur_ordered_id[0])

        def break_ordered() -> None:
            _in_ordered[0] = False
            _cur_ordered_id[0] = None

        # Shared bullet list (bullets don't need restart semantics as strongly)
        add_props(doc, "/numbering", typ="num", props={"id": 902, "format": "bullet", "start": 1})

        def list_props_only(props: dict) -> dict:
            """List numId only — visual formatting comes from the named paragraph style."""
            ls = (props or {}).get("listStyle")
            if ls == "ordered":
                return {"numId": ensure_ordered_num()}
            if ls == "bullet":
                break_ordered()
                return {"numId": 902}
            break_ordered()
            return {}

        def emit_styled_para(
            brole: str,
            txt: str,
            bprops: dict,
            block: dict,
            *,
            content_runs: list | None = None,
        ) -> None:
            """Named style=role_id; preview/seed 带 {role_id} 标记，gold 不带但应用 runs。"""
            para_i[0] += 1
            pi = para_i[0]
            bid = block.get("block_id") or f"P{pi}"
            raw = strip_inline_markup(str(txt or "")).strip()
            # 去掉正文里误带的 {ParaXxx}（gold 尤其要干净）
            _rid, raw = strip_para_marker(raw)
            raw = strip_inline_markup(raw).strip()
            # Auto list (numId) already paints 1./• — drop duplicate manual prefixes in body.
            ls = (bprops or {}).get("listStyle")
            removed_prefix = ""
            if ls in ("ordered", "bullet"):
                raw, removed_prefix = strip_manual_list_prefix(raw)
            # gold：跳过空段，避免只剩样式名/空行
            if not annotated and not raw:
                para_i[0] -= 1
                return
            marked = with_para_marker(brole, raw) if annotated else raw
            # preview：大纲 expected_runs；gold：优先 content.runs（step5 已写子串），否则大纲
            if annotated:
                expected = _normalize_expected_runs(block.get("expected_runs"))
            else:
                expected = _normalize_expected_runs(
                    content_runs if content_runs is not None else block.get("expected_runs")
                )
            if removed_prefix:
                _adjust_runs_after_prefix_strip(expected, removed_prefix)
            if expected and any(not er.get("text") for er in expected):
                org = block.get("organization_note") or ""
                quotes = re.findall(r'[“"「『]([^”"」』]{1,24})[”"」』]', f"{raw} {org}")
                qi = 0
                for er in expected:
                    if er.get("text"):
                        continue
                    picked = ""
                    for q in quotes:
                        if q and q in marked:
                            picked = q
                            break
                    if not picked and qi < len(quotes):
                        picked = quotes[qi]
                        qi += 1
                    if not picked:
                        picked = (roles.get(er["role"]) or {}).get("example") or ""
                    er["text"] = strip_inline_markup(str(picked)).strip()
            # gold + specimen/template: 段首「标签：」自动挂加粗 run（与 step6 同规律）
            # 用 raw 匹配领词（annotated 时 marked 带 {Para*} 前缀）
            if (not annotated) or using_specimen:
                expected = _maybe_add_lead_bold_run(expected, raw, roles)
            # 子串必须落在本段正文；找不到的 run 丢掉（preview/step5 与 gold/step6 一致）
            if expected:
                expected = [
                    er for er in expected
                    if er.get("text") and er["text"] in marked
                ]

            list_only = list_props_only(bprops)
            # Late-create style if outline used a Para* not registered at startup
            if (
                brole.startswith("Para")
                and brole not in para_styles_ready
            ):
                # use .update (not |=): |= rebinds the name and makes it local → UnboundLocalError
                extra = _ensure_para_styles(doc, roles, also={brole})
                para_styles_ready.update(extra)
            if not expected:
                add_props(doc, "/body", typ="paragraph",
                          props={"text": marked, "style": brole, **list_only})
            else:
                spans = _run_inline_spans(marked, expected, roles, run_props)
                if not spans:
                    add_props(doc, "/body", typ="paragraph",
                              props={"text": marked, "style": brole, **list_only})
                else:
                    first_txt, first_rp = spans[0]
                    padd = {"text": first_txt, "style": brole, **list_only}
                    if first_rp:
                        padd.update(first_rp)
                    add_props(doc, "/body", typ="paragraph", props=padd)
                    for seg_txt, seg_rp in spans[1:]:
                        add_props(doc, "/body/p[last()]", typ="run",
                                  props={"text": seg_txt, **(seg_rp or {})})

            new_map["paragraph"].setdefault(brole, []).append(pi)
            if brole not in new_styles["paragraph"]:
                new_styles["paragraph"][brole] = {"style_id": brole, "para_index": pi}
            new_map["paragraph_objects"][bid] = {
                "para_index": pi,
                "style_id": brole,
                "marker": ("{" + brole + "}") if annotated else "",
            }
            if expected:
                new_map["runs"][bid] = [
                    {"role": er.get("role"), "text": er.get("text")}
                    for er in expected if er.get("role")
                ]
                for er in expected:
                    role = er.get("role")
                    if role and role not in new_styles["run"]:
                        new_styles["run"][role] = {"object_id": bid}

        if not sections:
            add_props(doc, "/body", typ="paragraph",
                      props={"text": f"（缺 {plan_path}，无法按大纲渲染预览）", "color": "#A0A0A0"})
            dump_json(template_dir / "styles.json", new_styles)
            dump_json(template_dir / "map.json", new_map)
            try:
                validate_docx(doc)
            finally:
                close_docx(doc)
                _close_batch()
            return doc

        n = len(sections)
        for i, sec in enumerate(sections):
            inst = sec.get("instance_id")
            role_id = sec.get("role_id")
            title = sec.get("title") or role_id
            for b in outline_map.get(inst, []):
                if should_skip_block(b, exhibit_plan, roles):
                    continue
                brole = _style_rid_for_block(b, roles) or b.get("role_id")
                if is_section_style_block(b, roles):
                    continue
                cblk = content.get(b.get("block_id")) or {}
                bobj = rspec(brole).get("object")
                if bobj == "table":
                    from LongDocFmtEval.pipeline_auto.seed.caption_bind import (  # noqa: PLC0415
                        is_cover_info_table_role,
                    )
                    from LongDocFmtEval.pipeline_auto.seed.render_overrides import (  # noqa: PLC0415
                        should_skip_table_render,
                    )
                    has_tbl_content = isinstance(cblk, dict) and bool(cblk.get("table"))
                    skip_tbl = should_skip_table_render(
                        render_overrides,
                        is_cover_info=is_cover_info_table_role(brole),
                        gold_mode=gold_mode,
                        has_table_content=has_tbl_content,
                    )
                    if skip_tbl:
                        print(
                            f"[skip-table] {skip_tbl} "
                            f"block={b.get('block_id')} role={brole}"
                        )
                        break_ordered()
                        continue
                    tbl_spec = roles.get(brole) or {"props": rprops(brole)}
                    outline_tbl = None if gold_mode else table_content_from_outline_block(b)
                    if gold_mode and has_tbl_content:
                        grid_dicts = build_table_content(
                            doc, brole, tbl_spec, roles, pos_para,
                            cblk["table"], mark_cells=False,
                            warnings_out=table_cell_warnings,
                            block_id=str(b.get("block_id") or ""),
                        ) or []
                    elif outline_tbl:
                        # step5 specimen/preview: same fill path as step6 gold when
                        # table_ref / pipe main_point already carries real cells
                        grid_dicts = build_table_content(
                            doc, brole, tbl_spec, roles, pos_para,
                            outline_tbl, mark_cells=annotated,
                            warnings_out=table_cell_warnings,
                            block_id=str(b.get("block_id") or ""),
                        ) or []
                    elif not gold_mode:
                        grid_dicts = build_table(
                            doc, brole, tbl_spec, roles, pos_para,
                            block=b, mark_cells=annotated, locale=locale, theme=theme,
                            canonical_main_point=canonical_main_points.get(
                                str(b.get("block_id") or ""), "",
                            ),
                        ) or []
                    else:
                        grid_dicts = []
                    if not grid_dicts:
                        break_ordered()
                        continue
                    tbl_i[0] += 1
                    ti = tbl_i[0]
                    oid = str(b.get("block_id") or f"tbl_{ti}")
                    rendered_tables[oid] = {
                        "object_id": oid,
                        "role_id": brole,
                        "table_index": ti,
                        "pattern": "rendered",
                        "main_point": b.get("main_point") or "",
                        "grid": grid_dicts,
                    }
                    new_map["table_structure"].setdefault(brole, []).append(ti)
                    if brole not in new_styles["table_structure"]:
                        new_styles["table_structure"][brole] = ti

                    # Nested table style: structure + cell locators only (props live in role_params)
                    chrome_by_pos = llm_cells_chrome(tbl_spec)
                    tbl_style = new_styles["table"].setdefault(
                        brole,
                        {
                            "table_index": ti,
                            "table_structure": {"table": ti},
                            "table_cell": {},
                        },
                    )
                    tbl_map = new_map["table"].setdefault(
                        brole,
                        {"tables": [], "table_cell": {}},
                    )
                    if ti not in tbl_map["tables"]:
                        tbl_map["tables"].append(ti)

                    for ri, row in enumerate(grid_dicts, start=1):
                        for ci, cell in enumerate(row, start=1):
                            para_rid = str(
                                cell.get("paragraph_style")
                                or cell.get("role_id")
                                or "ParaTblDataCell"
                            )
                            raw_cs = str(
                                cell.get("cell_style") or cell.get("pos") or "data"
                            )
                            cell_style = resolve_cell_style_key(raw_cs, chrome_by_pos)
                            coord = f"{ti}:{ri}:{ci}"
                            # cell style under this table (locator only — no border/fill)
                            if cell_style not in tbl_style["table_cell"]:
                                tbl_style["table_cell"][cell_style] = {
                                    "table": ti, "row": ri, "col": ci,
                                }
                            tbl_map["table_cell"].setdefault(cell_style, [])
                            if coord not in tbl_map["table_cell"][cell_style]:
                                tbl_map["table_cell"][cell_style].append(coord)
                            # ParaTbl* remains a paragraph-style exemplar (text props)
                            new_map["table_cell"].setdefault(para_rid, []).append(coord)
                            entry = {
                                "table": ti, "row": ri, "col": ci,
                                "cell_style": cell_style,
                                "table_role": brole,
                            }
                            if para_rid not in new_styles["table_cell"]:
                                new_styles["table_cell"][para_rid] = entry
                    break_ordered()
                elif bobj == "image":
                    img_i[0] += 1
                    ii = img_i[0]
                    p = rprops(brole)
                    fid = next_fid(b)
                    info = asset_info(b, fid)
                    asset = resolve_asset_file(template_dir, info.get("asset"))
                    align = info.get("align") or p.get("hAlign") or "center"
                    if asset and asset.exists():
                        # 尺寸以 T*（人改回写）为准，assets.json 仅兜底
                        w = p.get("width") or info.get("width") or "12cm"
                        h = p.get("height") or info.get("height") or "8cm"
                        add_props(doc, "/body", typ="paragraph",
                                  props={"align": align, "spaceBefore": "6pt", "spaceAfter": "6pt"})
                        img_props = {"src": str(asset.resolve()), "width": w, "height": h}
                        add_props(doc, "/body/p[last()]", typ="image", props=img_props)
                    else:
                        # gold：无图时仍给可读占位，但不写 {Img*} 标记
                        ph_body = (
                            f"［{fid} 图片占位 "
                            f"{p.get('width') or info.get('width') or '?'}×"
                            f"{p.get('height') or info.get('height') or '?'}］"
                        )
                        ph = with_para_marker(brole, ph_body) if annotated else ph_body
                        style_props = {"text": ph, "align": align, "color": "#808080"}
                        if brole in roles:
                            style_props["style"] = brole
                        add_props(doc, "/body", typ="paragraph", props=style_props)
                    new_map["image"].setdefault(brole, []).append(ii)
                    if brole not in new_styles["image"]:
                        new_styles["image"][brole] = ii
                    break_ordered()
                else:
                    bprops = dict(rprops(brole))
                    if (not annotated) and cblk.get("text"):
                        text = cblk.get("text")
                    else:
                        text = _heading_text(
                            b, bprops, brole, rexample(brole),
                            prefer_example=bool(annotated) and not using_specimen,
                        )
                    content_runs = cblk.get("runs") if isinstance(cblk, dict) else None
                    if not annotated and isinstance(text, str):
                        chunks = _split_gold_text_chunks(
                            text, list_style=bprops.get("listStyle"),
                        )
                        if not chunks:
                            continue
                        for chunk in chunks:
                            # 每段传入同一 runs 列表；emit 内只保留本段含有的子串
                            emit_styled_para(
                                brole, chunk, bprops, b, content_runs=content_runs,
                            )
                    else:
                        emit_styled_para(
                            brole, text, bprops, b, content_runs=content_runs,
                        )

            sp = _nonempty(rprops(role_id))
            coerce_officecli_prop_keys(sp)
            strip_wrong_layer_keys(sp, obj="section")
            sp = {
                k: v for k, v in sp.items()
                if k in SECTION_OFFICECLI_KEYS and not isinstance(v, (dict, list))
            }
            sp.pop("vAlign", None)  # 禁止页面垂直对齐；默认 top，避免 both 撑散少段内容
            ov = sec.get("instance_overrides") or {}
            for k in ("pageStart", "pageNumFmt", "titlePage"):
                if k not in ov:
                    continue
                if str(ov[k]).lower() == "none":
                    sp.pop(k, None)
                    continue
                if k == "pageStart":
                    # officecli 只要整数；大纲偶发把罗马显示值 "i" 误写入 pageStart
                    cleaned = _sanitize_page_start(ov[k])
                    if cleaned is None:
                        sp.pop(k, None)
                    else:
                        sp[k] = cleaned
                elif k == "pageNumFmt":
                    cleaned = _sanitize_page_num_fmt(ov[k])
                    if cleaned is None:
                        sp.pop(k, None)
                    else:
                        sp[k] = cleaned
                else:
                    sp[k] = ov[k]
            sp.setdefault("type", "nextPage")
            sp["type"] = _sanitize_section_break_type(sp.get("type"))
            # section 角色 props 里的 pageStart / pageNumFmt 同样校正
            if "pageStart" in sp:
                cleaned = _sanitize_page_start(sp["pageStart"])
                if cleaned is None:
                    sp.pop("pageStart", None)
                else:
                    sp["pageStart"] = cleaned
            if "pageNumFmt" in sp:
                cleaned = _sanitize_page_num_fmt(sp["pageNumFmt"])
                if cleaned is None:
                    sp.pop("pageNumFmt", None)
                else:
                    sp["pageNumFmt"] = cleaned

            if i < n - 1:
                add_sp = {k: v for k, v in sp.items() if k not in _SET_ONLY_SECTION_KEYS}
                add_props(doc, "/body", typ="section", props=add_sp)
                secpath = f"/section[{i + 1}]"
                extra = {k: v for k, v in sp.items() if k in _SET_ONLY_SECTION_KEYS}
                if extra:
                    set_props(doc, secpath, extra)
            else:
                secpath = f"/section[{n}]"
                if sp:
                    set_props(doc, secpath, sp)
            add_props(doc, secpath, typ="header",
                      props=_section_hf_props(rspec(role_id), "header", title))
            add_props(doc, secpath, typ="footer",
                      props=_section_hf_props(rspec(role_id), "footer", title))
            if str(sp.get("titlePage")).lower() == "true":
                add_props(doc, secpath, typ="header",
                          props=_section_hf_props(rspec(role_id), "header_first", title,
                                                  default_type="first"))
            break_ordered()

        # Seed-compatible sidecars for human edit + re-render / sync_map_from_markers
        dump_json(template_dir / "styles.json", new_styles)
        dump_json(template_dir / "map.json", new_map)
        img_in_ss = sorted(
            r for r, spec in (roles or {}).items()
            if isinstance(spec, dict) and spec.get("object") == "image"
        )
        tbl_in_ss = sorted(
            r for r, spec in (roles or {}).items()
            if isinstance(spec, dict) and spec.get("object") == "table"
        )
        img_in_doc = sorted((new_styles.get("image") or {}).keys())
        tbl_in_doc = sorted(
            (new_styles.get("table_structure") or {}).keys()
            or (new_styles.get("table") or {}).keys()
        )
        print(
            f"[styles] 本 docx 定位器 table={len(tbl_in_doc)}/{len(tbl_in_ss)} "
            f"image={len(img_in_doc)}/{len(img_in_ss)} "
            f"（stylesheet 种数；preview 只限制每种 style 的实例张数≤2，不砍种数）"
        )
        missing_img = [r for r in img_in_ss if r not in (new_styles.get("image") or {})]
        if missing_img:
            print(f"[styles] 未写入本 docx 的 Img*（大纲未挂块或本篇未示范）: {missing_img}")
        # tables.json must use the same absolute indices as styles/map/docx (exhibit-safe)
        if rendered_tables:
            dump_json(template_dir / "tables.json", rendered_tables)
            print(f"[tables] wrote tables.json ({len(rendered_tables)} tables, indices match docx)")
        warn_path = template_dir / "table-cell-warning.md"
        write_table_cell_warning_md(warn_path, table_cell_warnings, locale=locale)
        if table_cell_warnings:
            print(
                f"[warn] table-cell paragraph_style 异常 {len(table_cell_warnings)} 处 → "
                f"{warn_path.name}（未静默改写，请人工核对）"
            )

        try:
            validate_docx(doc)
        finally:
            close_docx(doc)
            _close_batch()
        return doc
    except Exception:
        try:
            close_docx(doc)
        except Exception:
            pass
        _close_batch(sys.exc_info())
        raise


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--template-dir", required=True, help="step3 产物目录（含 template.json）")
    ap.add_argument(
        "--specimen",
        action="store_true",
        help="可选：按 stylesheet 抽样（每种 role ≤1）；默认按完整大纲渲染 preview",
    )
    args = ap.parse_args()
    d = Path(args.template_dir)
    if not d.exists():
        alt = Path("LongDocFmtEval") / args.template_dir
        if not str(args.template_dir).replace("\\", "/").startswith("LongDocFmtEval/") and alt.exists():
            d = alt
        else:
            ap.error(f"目录不存在: {d}")
    from LongDocFmtEval.pipeline_auto.auto_paths import resolve_work_file  # noqa: PLC0415
    if not (
        resolve_work_file(d, "role_params.json").is_file()
        or resolve_work_file(d, "template.json").is_file()
    ):
        ap.error(f"缺 role_params.json / template.json: {d}")
    out = render(d, assets_name="assets_preview.json", specimen=bool(args.specimen))
    print(f"[docx] {out}")


if __name__ == "__main__":
    main()
