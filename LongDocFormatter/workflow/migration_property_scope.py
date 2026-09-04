"""Canonical Path A format property scope — shared by modify (format_migration) and eval.

Three layers::

1. **extract / align** — may read extra signals (``semantic_text``, ``style_name``,
   ``runs_signature``, full ``table_format``, etc.) for clustering and LLM only.
2. **modify** — functions in this module prune template ``format_config`` to the
   exact write surface before calling officecli Writers.
3. **eval** — :func:`prune_*` / :func:`extract_document_format` use the **same**
   pruners; paragraphs also require :func:`paragraph_in_style_track`.

``config/migration_config.yaml`` ``modify`` flags (e.g. ``apply_runs``,
``table_structure.apply_cells=false``) are reflected here.
"""

from __future__ import annotations

from typing import Any

from tools.migration.table_cell_visual import (
    cell_visual_format,
    normalize_cell_visual_for_write,
    normalize_table_structure_format_config,
)
from LongDocFormatter.officecli.read.image_schema import build_image_format
from LongDocFormatter.officecli.read.paper_format import normalize_page_format, page_format_for_dataset_migration
from LongDocFormatter.officecli.read.table_schema import compact_borders_for_migration

# ---------------------------------------------------------------------------
# Paragraph text_format — Path A paragraph modify (no style_name / list / mark_rpr)
# ---------------------------------------------------------------------------

PARAGRAPH_PATH_A_KEYS = frozenset(
    {
        "base_font",
        "advanced_font",
        "alignment",
        "outline_level",
        "pagination_control",
        "spacing",
        "indent",
        "runs",
    }
)

BASE_FONT_EVAL_KEYS = frozenset(
    {
        "name",
        "name_ascii",
        "name_far_east",
        "size",
        "bold",
        "italic",
        "underline",
        "color",
        "highlight",
    }
)

ADVANCED_FONT_EVAL_KEYS = frozenset(
    {
        "strike",
        "double_strike",
        "caps",
        "small_caps",
        "superscript",
        "subscript",
        "char_spacing",
        "emboss",
        "imprint",
        "shadow",
        "outline",
        "vanish",
    }
)

ALIGNMENT_EVAL_KEYS = frozenset({"alignment"})
OUTLINE_LEVEL_EVAL_KEYS = frozenset({"outline_level"})
PAGINATION_CONTROL_EVAL_KEYS = frozenset(
    {
        "widow_control",
        "keep_with_next",
        "keep_together",
        "page_break_before",
        "word_wrap",
        "contextual_spacing",
    }
)
SPACING_EVAL_KEYS = frozenset(
    {"line_spacing", "line_spacing_rule", "before", "after"}
)
INDENT_EVAL_KEYS = frozenset(
    {
        "left",
        "right",
        "first_line",
        "hanging",
        # Character-unit indents mirror absolute length; Path A / eval use pt only.
        # ("first_line_chars", "hanging_chars" omitted)
    }
)

# ---------------------------------------------------------------------------
# Page — Path A page modify groups
# ---------------------------------------------------------------------------

PAGE_PATH_A_TOP_KEYS = frozenset(
    {
        "margin",
        "paper",
        "grid",
        "columns",
        "header_footer_layout",
        "header",
        "footer",
    }
)
PAGE_EVAL_TOP_KEYS = PAGE_PATH_A_TOP_KEYS
PARAGRAPH_EVAL_FORMAT_KEYS = PARAGRAPH_PATH_A_KEYS

MARGIN_EVAL_KEYS = frozenset({"top", "bottom", "left", "right"})
PAPER_EVAL_KEYS = frozenset({"width", "height"})
GRID_EVAL_KEYS = frozenset({"layout_mode", "line_pitch", "char_space"})
COLUMNS_EVAL_KEYS = frozenset(
    {"count", "spacing", "equal_width", "separator", "col_widths"}
)
HEADER_FOOTER_LAYOUT_EVAL_KEYS = frozenset({"header_distance", "footer_distance"})

# ---------------------------------------------------------------------------
# Table / image
# ---------------------------------------------------------------------------

TABLE_LAYOUT_EVAL_KEYS = frozenset({"width", "align", "indent"})
ROW_EVALUATION_KEYS: frozenset[str] = frozenset()
TABLE_EXCLUDED_TOP_KEYS = frozenset({"structure"})

IMAGE_HOST_MIGRATION_KEYS = frozenset({"alignment", "pagination_control"})
IMAGE_EXCLUDED_TOP_KEYS = frozenset({"metadata", "preview", "section_index"})

# Object metadata fields (not format leaves).
OBJECT_META_KEYS = frozenset(
    {
        "path",
        "paragraph_index",
        "row_index",
        "col_index",
        "table_index",
        "image_index",
        "section_index",
        "kind",
        "text",
        "in_table",
        "preview",
        "row_count",
    }
)

# Leaf key suffixes skipped everywhere (read-only or not written by Path A migration).
EXCLUDED_LEAF_SUFFIXES = frozenset(
    {
        "style_name",
        "mark_rpr_font",
        "list",
        "paper.orientation",
        "paper.size",
        "columns.first_width",
        "section_type",
        "page_start",
        "page_num_fmt",
        "row_format",
        "table_format.pagination",
        "table_format.style",
        "indent.first_line_chars",
        "indent.hanging_chars",
    }
)

ROW_MIGRATION_KEYS = ROW_EVALUATION_KEYS


def _prune_dict(data: dict[str, Any], allowed: frozenset[str]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if key in allowed}


def _prune_nested_group(
    group: dict[str, Any] | None,
    allowed: frozenset[str],
) -> dict[str, Any]:
    if not group:
        return {}
    return _prune_dict(group, allowed)


def text_format_for_path_a(text_format: dict[str, Any] | None) -> dict[str, Any]:
    """Paragraph ``text_format`` written by Path A paragraph modify."""
    if not text_format:
        return {}
    out: dict[str, Any] = {}
    for key in PARAGRAPH_EVAL_FORMAT_KEYS:
        if key not in text_format:
            continue
        value = text_format[key]
        if key == "base_font":
            pruned = _prune_nested_group(value, BASE_FONT_EVAL_KEYS)
            if pruned:
                out[key] = pruned
        elif key == "advanced_font":
            pruned = _prune_nested_group(value, ADVANCED_FONT_EVAL_KEYS)
            if pruned:
                out[key] = pruned
        elif key == "alignment":
            pruned = _prune_nested_group(value, ALIGNMENT_EVAL_KEYS)
            if pruned:
                out[key] = pruned
        elif key == "outline_level":
            pruned = _prune_nested_group(value, OUTLINE_LEVEL_EVAL_KEYS)
            if pruned:
                out[key] = pruned
        elif key == "pagination_control":
            pruned = _prune_nested_group(value, PAGINATION_CONTROL_EVAL_KEYS)
            if pruned:
                out[key] = pruned
        elif key == "spacing":
            pruned = _prune_nested_group(value, SPACING_EVAL_KEYS)
            if pruned:
                out[key] = pruned
        elif key == "indent":
            pruned = _prune_nested_group(value, INDENT_EVAL_KEYS)
            if pruned:
                out[key] = pruned
        elif key == "runs":
            runs_out: list[dict[str, Any]] = []
            for run in value or []:
                if not isinstance(run, dict):
                    continue
                run_copy: dict[str, Any] = {}
                if "text" in run:
                    run_copy["text"] = run["text"]
                if "base_font" in run:
                    bf = _prune_nested_group(run.get("base_font"), BASE_FONT_EVAL_KEYS)
                    if bf:
                        run_copy["base_font"] = bf
                if "advanced_font" in run:
                    af = _prune_nested_group(run.get("advanced_font"), ADVANCED_FONT_EVAL_KEYS)
                    if af:
                        run_copy["advanced_font"] = af
                if run_copy:
                    runs_out.append(run_copy)
            if runs_out:
                out[key] = runs_out
        else:
            out[key] = value
    return out


def page_format_for_path_a(page_format: dict[str, Any] | None) -> dict[str, Any]:
    """Page groups written by Path A page modify."""
    pf = page_format_for_dataset_migration(normalize_page_format(page_format))
    if not pf:
        return {}
    out: dict[str, Any] = {}
    if "margin" in pf:
        margin = _prune_nested_group(pf.get("margin"), MARGIN_EVAL_KEYS)
        if margin:
            out["margin"] = margin
    if "paper" in pf:
        paper = _prune_nested_group(pf.get("paper"), PAPER_EVAL_KEYS)
        if paper:
            out["paper"] = paper
    if "grid" in pf:
        grid = _prune_nested_group(pf.get("grid"), GRID_EVAL_KEYS)
        if grid:
            out["grid"] = grid
    if "columns" in pf:
        columns = _prune_nested_group(pf.get("columns"), COLUMNS_EVAL_KEYS)
        if columns:
            out["columns"] = columns
    if "header_footer_layout" in pf:
        layout = _prune_nested_group(
            pf.get("header_footer_layout"),
            HEADER_FOOTER_LAYOUT_EVAL_KEYS,
        )
        if layout:
            out["header_footer_layout"] = layout
    if "header" in pf:
        out["header"] = _prune_header_footer_block(pf.get("header"))
    if "footer" in pf:
        out["footer"] = _prune_header_footer_block(pf.get("footer"))
    return out


def table_format_for_path_a(table_format: dict[str, Any] | None) -> dict[str, Any]:
    """Table-level format written by Path A ``table_structure`` modify."""
    tf = dict(table_format or {})
    out: dict[str, Any] = {}
    # Never copy ``style``: styleId is document-local (template ``a3`` ≠ content ``a3``).
    layout = _prune_nested_group(tf.get("layout"), TABLE_LAYOUT_EVAL_KEYS)
    if layout:
        out["layout"] = layout
    borders = compact_borders_for_migration(tf.get("borders"), target="table")
    if borders:
        out["borders"] = borders
    return out


def _prune_header_footer_slot(slot: dict[str, Any] | None) -> dict[str, Any]:
    from LongDocFormatter.officecli.modify.page_schema import page_num_fmt_writable

    if not slot:
        return {}
    out = dict(slot)
    page_number = dict(out.get("page_number") or {})
    fmt = page_number.get("format")
    if fmt is not None and not page_num_fmt_writable(fmt):
        page_number.pop("format", None)
    if page_number:
        out["page_number"] = page_number
    elif "page_number" in out:
        out.pop("page_number")
    return out


def _prune_header_footer_block(block: dict[str, Any] | None) -> dict[str, Any]:
    if not block:
        return {}
    out = dict(block)
    for slot_key in ("primary", "first", "even"):
        if slot_key in out:
            out[slot_key] = _prune_header_footer_slot(out.get(slot_key))
    return out


def host_paragraph_for_path_a(host: dict[str, Any] | None) -> dict[str, Any]:
    if not host:
        return {}
    out: dict[str, Any] = {}
    for key in IMAGE_HOST_MIGRATION_KEYS:
        if key not in host:
            continue
        if key == "alignment":
            pruned = _prune_nested_group(host.get("alignment"), ALIGNMENT_EVAL_KEYS)
            if pruned:
                out[key] = pruned
        elif key == "pagination_control":
            pruned = _prune_nested_group(
                host.get("pagination_control"),
                PAGINATION_CONTROL_EVAL_KEYS,
            )
            if pruned:
                out[key] = pruned
    return out


# Eval aliases (same functions)
text_format_for_evaluation = text_format_for_path_a
page_format_for_evaluation = page_format_for_path_a
table_format_for_evaluation = table_format_for_path_a


def paragraph_format_config_for_modify(format_config: dict[str, Any] | None) -> dict[str, Any]:
    """Prune paragraph ``style.format_config`` to Path A write payload."""
    return text_format_for_path_a(format_config)


def page_format_config_for_modify(format_config: dict[str, Any] | None) -> dict[str, Any]:
    return page_format_for_path_a(format_config)


def table_structure_config_for_modify(format_config: dict[str, Any] | None) -> dict[str, Any]:
    """Officecli table JSON for ``WordTableWriter.apply_table`` (table_format only)."""
    fc = dict(format_config or {})
    return normalize_table_structure_format_config(
        {
            "table_format": table_format_for_path_a(fc.get("table_format")),
            "rows": [],
        }
    )


def table_cell_format_config_for_modify(format_config: dict[str, Any] | None) -> dict[str, Any]:
    return cell_visual_format(dict(format_config or {}))


def image_format_config_for_modify(format_config: dict[str, Any] | None) -> dict[str, Any]:
    """
    Normalize image style payload for :class:`~LongDocFormatter.officecli.modify.WordImageWriter`.

    Same writable surface as dataset image migration:

    - ``image_format.size`` (width / height; optional write helpers)
    - ``host_paragraph.alignment`` / ``pagination_control`` (host paragraph only;
      not page header/footer)
    """
    fc = dict(format_config or {})
    raw_image = dict(fc.get("image_format") or {})
    # Drop non-writable metadata if callers nested it under image_format.
    raw_image.pop("metadata", None)
    return {
        "image_format": build_image_format(raw_image),
        "host_paragraph": host_paragraph_for_path_a(fc.get("host_paragraph")),
    }


def prune_page_format(page_format: dict[str, Any] | None) -> dict[str, Any]:
    return page_format_for_path_a(page_format)


def prune_text_format(text_format: dict[str, Any] | None) -> dict[str, Any]:
    return text_format_for_path_a(text_format)


def prune_table_object(table: dict[str, Any]) -> dict[str, Any]:
    out = dict(table)
    for key in TABLE_EXCLUDED_TOP_KEYS:
        out.pop(key, None)

    out["table_format"] = table_format_for_path_a(dict(out.get("table_format") or {}))

    rows_out: list[dict[str, Any]] = []
    for row in out.get("rows") or []:
        if not isinstance(row, dict):
            continue
        row_copy = {
            "row_index": row.get("row_index"),
            "path": row.get("path"),
            "cells": [],
        }
        for cell in row.get("cells") or []:
            if not isinstance(cell, dict):
                continue
            cell_copy = {
                "row_index": cell.get("row_index"),
                "col_index": cell.get("col_index"),
                "path": cell.get("path"),
                "cell_format": cell_visual_format(cell.get("cell_format") or {}),
                "paragraphs": [],
            }
            for para in cell.get("paragraphs") or []:
                if not isinstance(para, dict):
                    continue
                cell_copy["paragraphs"].append(
                    {
                        "path": para.get("path"),
                        "paragraph_index": para.get("paragraph_index"),
                        "text_format": prune_text_format(para.get("text_format") or {}),
                    }
                )
            row_copy["cells"].append(cell_copy)
        rows_out.append(row_copy)
    out["rows"] = rows_out
    return out


def prune_image_object(image: dict[str, Any]) -> dict[str, Any]:
    raw_fmt = dict(image.get("image_format") or {})
    raw_fmt.pop("metadata", None)
    office_fmt = dict(image.get("format") or {}) if isinstance(image.get("format"), dict) else {}
    # Nested size from reader wins over flat officecli fields when both present.
    merged = {**office_fmt, **raw_fmt}
    out = {k: v for k, v in image.items() if k not in IMAGE_EXCLUDED_TOP_KEYS}
    out["image_format"] = build_image_format(merged)
    out["host_paragraph"] = host_paragraph_for_path_a(image.get("host_paragraph"))
    return out


def leaf_key_allowed(flat_key: str) -> bool:
    for suffix in EXCLUDED_LEAF_SUFFIXES:
        if flat_key.endswith(suffix) or f".{suffix}" in flat_key:
            return False
    if ".mark_rpr_font" in flat_key:
        return False
    if ".text_format.list" in flat_key or flat_key.endswith(".list"):
        return False
    if ".row_format." in flat_key or flat_key.endswith(".row_format"):
        return False
    if ".table_format.pagination" in flat_key or flat_key.endswith("table_format.pagination"):
        return False
    if ".table_format.style" in flat_key or flat_key.endswith("table_format.style"):
        return False
    if ".columns.first_width" in flat_key or flat_key.endswith("columns.first_width"):
        return False
    return True
