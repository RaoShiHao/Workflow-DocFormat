"""Map page_format groups to officecli section/document properties."""

from __future__ import annotations

from typing import Any

from LongDocFormatter.officecli.read.format_schema import _scalar_from_fmt
from LongDocFormatter.officecli.read.page_schema import page_number_section_overrides, sanitize_grid_for_layout

from ._format_props import assign_if_changed

PAGE_CLEAR_VALUES: dict[str, Any] = {
    "colWidths": "none",
    "docGrid.type": "default",
    "docGrid.linePitch": "",
    "docGrid.charSpace": "",
    "separator": False,
    "cols.equalWidth": False,
    "pageStart": "none",
    "pageNumFmt": "none",
}

# officecli ``set pageNumFmt`` values (from officecli invalid_value hints).
# COM writes page numbering via ``Footer.PageNumbers.NumberStyle`` (WdPageNumberStyle),
# e.g. wdPageNumberStyleNumberInDash (57) for gov-doc "- 1 -" styles. Those COM/OOXML
# names (``numberInDash``, etc.) may be readable but are not writable via officecli.
OFFICECLI_PAGE_NUM_FMT_VALUES = frozenset(
    {
        "decimal",
        "lowerRoman",
        "upperRoman",
        "lowerLetter",
        "upperLetter",
        "bullet",
        "hindiNumbers",
        "hindiVowels",
        "arabicAlpha",
        "arabicAbjad",
        "thaiCounting",
        "chineseCounting",
        "japaneseCounting",
        "koreanCounting",
        "ideographDigital",
        "none",
    }
)


def page_num_fmt_writable(value: Any) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    if not text:
        return False
    return text in OFFICECLI_PAGE_NUM_FMT_VALUES


def skipped_page_num_fmt_warnings(page_format: dict[str, Any]) -> list[str]:
    """Warn when template page numbering format cannot be written via officecli."""
    warnings: list[str] = []
    candidates: list[Any] = []
    if "page_num_fmt" in page_format:
        candidates.append(page_format.get("page_num_fmt"))
    overrides = page_number_section_overrides(page_format)
    if "page_num_fmt" in overrides and "page_num_fmt" not in page_format:
        candidates.append(overrides.get("page_num_fmt"))
    for value in candidates:
        if value is None:
            continue
        text = str(value).strip()
        if not text or page_num_fmt_writable(text):
            continue
        warnings.append(
            f"Skipped unsupported pageNumFmt {text!r}; "
            "officecli does not support this format (COM uses Footer.PageNumbers.NumberStyle)."
        )
    return warnings


def _assign_page_num_fmt_if_supported(
    props: dict[str, Any],
    value: Any,
    *,
    source_value: Any = None,
) -> None:
    if value is not None:
        text = str(value).strip()
        if text and not page_num_fmt_writable(text):
            return
        if text:
            value = text
    assign_page_prop(
        props,
        "pageNumFmt",
        value,
        source_value=source_value,
    )


def assign_page_prop(
    props: dict[str, Any],
    office_key: str,
    value: Any,
    *,
    clear_value: Any | None = None,
    source_value: Any = None,
) -> None:
    effective_clear = (
        clear_value if clear_value is not None else PAGE_CLEAR_VALUES.get(office_key)
    )
    assign_if_changed(
        props,
        office_key,
        value,
        source_value,
        clear_value=effective_clear,
    )


def columns_to_section_props(
    columns: dict[str, Any],
    *,
    source_columns: dict[str, Any] | None = None,
) -> dict[str, Any]:
    props: dict[str, Any] = {}
    if not columns:
        return props
    source_columns = source_columns or {}

    if "count" in columns:
        assign_page_prop(
            props,
            "columns",
            columns.get("count"),
            source_value=source_columns.get("count"),
        )
    if "spacing" in columns:
        assign_page_prop(
            props,
            "columnSpace",
            columns.get("spacing"),
            clear_value=0,
            source_value=source_columns.get("spacing"),
        )
    if "equal_width" in columns:
        assign_page_prop(
            props,
            "cols.equalWidth",
            columns.get("equal_width"),
            source_value=source_columns.get("equal_width"),
        )
    if "separator" in columns:
        assign_page_prop(
            props,
            "separator",
            columns.get("separator"),
            source_value=source_columns.get("separator"),
        )
    if "col_widths" in columns:
        assign_page_prop(
            props,
            "colWidths",
            columns.get("col_widths"),
            source_value=source_columns.get("col_widths"),
        )

    return props


def _doc_grid_raw(section_fmt: dict[str, Any] | None) -> dict[str, Any]:
    if not section_fmt:
        return {}
    return {
        "line_pitch": _scalar_from_fmt(section_fmt, "docGrid.linePitch"),
        "char_space": _scalar_from_fmt(section_fmt, "docGrid.charSpace"),
        "type": _scalar_from_fmt(section_fmt, "docGrid.type"),
    }


def _clear_doc_grid_pitch(
    props: dict[str, Any],
    office_key: str,
    *,
    raw_value: Any,
) -> None:
    if raw_value is None:
        return
    assign_page_prop(
        props,
        office_key,
        None,
        clear_value=PAGE_CLEAR_VALUES[office_key],
        source_value=raw_value,
    )


def grid_to_section_props(
    grid: dict[str, Any],
    *,
    source_grid: dict[str, Any] | None = None,
    source_section_fmt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Write document grid via ``docGrid.type`` (COM / TemplateAgent semantics).

    Only fields valid for ``layout_mode`` are written; stale pitches on the target
    section are cleared when switching to a narrower grid mode.
    """
    props: dict[str, Any] = {}
    grid = sanitize_grid_for_layout(grid)
    if not grid:
        return props
    source_grid = sanitize_grid_for_layout(source_grid)
    raw = _doc_grid_raw(source_section_fmt)

    layout_mode = grid.get("layout_mode")
    source_layout = source_grid.get("layout_mode")

    if layout_mode in (None, "none"):
        assign_page_prop(
            props,
            "docGrid.type",
            None,
            source_value=raw.get("type") or source_layout,
        )
        # TemplateAgent: LayoutMode=0 does not touch LinesPage/CharsLine. Clearing both
        # pitches via officecli can corrupt the section; type=default is sufficient.
        return props

    if layout_mode == "lines_only":
        assign_if_changed(props, "docGrid.type", "lines", raw.get("type") or source_layout)
        if grid.get("line_pitch") is not None:
            assign_if_changed(
                props,
                "docGrid.linePitch",
                grid.get("line_pitch"),
                raw.get("line_pitch") if raw.get("line_pitch") is not None else source_grid.get("line_pitch"),
            )
        _clear_doc_grid_pitch(props, "docGrid.charSpace", raw_value=raw.get("char_space"))
        return props

    if layout_mode == "lines_and_chars":
        assign_if_changed(props, "docGrid.type", "linesAndChars", raw.get("type") or source_layout)
        if grid.get("line_pitch") is not None:
            assign_if_changed(
                props,
                "docGrid.linePitch",
                grid.get("line_pitch"),
                raw.get("line_pitch") if raw.get("line_pitch") is not None else source_grid.get("line_pitch"),
            )
        if grid.get("char_space") is not None:
            assign_if_changed(
                props,
                "docGrid.charSpace",
                grid.get("char_space"),
                raw.get("char_space") if raw.get("char_space") is not None else source_grid.get("char_space"),
            )
        return props

    if layout_mode == "snap_to_chars":
        assign_if_changed(props, "docGrid.type", "snapToChars", raw.get("type") or source_layout)
        if grid.get("line_pitch") is not None:
            assign_if_changed(
                props,
                "docGrid.linePitch",
                grid.get("line_pitch"),
                raw.get("line_pitch") if raw.get("line_pitch") is not None else source_grid.get("line_pitch"),
            )
        if grid.get("char_space") is not None:
            assign_if_changed(
                props,
                "docGrid.charSpace",
                grid.get("char_space"),
                raw.get("char_space") if raw.get("char_space") is not None else source_grid.get("char_space"),
            )
        return props

    return props


def section_props_from_page_format(
    page_format: dict[str, Any],
    *,
    source_page_format: dict[str, Any] | None = None,
    source_section_fmt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build officecli section props from ``page_format`` with optional source diff."""
    props: dict[str, Any] = {}
    source = source_page_format or {}

    margin = page_format.get("margin") or {}
    source_margin = source.get("margin") or {}
    for src, dst in (
        ("top", "marginTop"),
        ("bottom", "marginBottom"),
        ("left", "marginLeft"),
        ("right", "marginRight"),
    ):
        if src in margin:
            assign_page_prop(
                props,
                dst,
                margin.get(src),
                clear_value=0,
                source_value=source_margin.get(src),
            )

    paper = page_format.get("paper") or {}
    source_paper = source.get("paper") or {}
    for src, dst in (
        ("width", "pageWidth"),
        ("height", "pageHeight"),
    ):
        if src in paper:
            assign_page_prop(
                props,
                dst,
                paper.get(src),
                source_value=source_paper.get(src),
            )

    props.update(
        columns_to_section_props(
            page_format.get("columns") or {},
            source_columns=source.get("columns") or {},
        )
    )
    props.update(
        grid_to_section_props(
            page_format.get("grid") or {},
            source_grid=source.get("grid") or {},
            source_section_fmt=source_section_fmt,
        )
    )

    layout = page_format.get("header_footer_layout") or {}
    source_layout = source.get("header_footer_layout") or {}
    if "header_distance" in layout:
        assign_page_prop(
            props,
            "marginHeader",
            layout.get("header_distance"),
            clear_value=0,
            source_value=source_layout.get("header_distance"),
        )
    if "footer_distance" in layout:
        assign_page_prop(
            props,
            "marginFooter",
            layout.get("footer_distance"),
            clear_value=0,
            source_value=source_layout.get("footer_distance"),
        )

    header = page_format.get("header") or {}
    footer = page_format.get("footer") or {}
    source_header = source.get("header") or {}
    source_footer = source.get("footer") or {}
    different_first = header.get("different_first_page")
    if different_first is None and "different_first_page" in footer:
        different_first = footer.get("different_first_page")
    source_different_first = source_header.get("different_first_page")
    if source_different_first is None and "different_first_page" in source_footer:
        source_different_first = source_footer.get("different_first_page")
    if "different_first_page" in header or "different_first_page" in footer:
        assign_page_prop(
            props,
            "titlePage",
            different_first,
            clear_value=False,
            source_value=source_different_first,
        )

    if "page_start" in page_format:
        assign_page_prop(
            props,
            "pageStart",
            page_format.get("page_start"),
            source_value=source.get("page_start"),
        )
    if "page_num_fmt" in page_format:
        _assign_page_num_fmt_if_supported(
            props,
            page_format.get("page_num_fmt"),
            source_value=source.get("page_num_fmt"),
        )

    page_overrides = page_number_section_overrides(page_format)
    if "page_num_fmt" in page_overrides and "page_num_fmt" not in page_format:
        _assign_page_num_fmt_if_supported(
            props,
            page_overrides.get("page_num_fmt"),
            source_value=source.get("page_num_fmt"),
        )
    if "page_start" in page_overrides and "page_start" not in page_format:
        assign_page_prop(
            props,
            "pageStart",
            page_overrides.get("page_start"),
            source_value=source.get("page_start"),
        )
    if "section_type" in page_format:
        assign_page_prop(
            props,
            "type",
            page_format.get("section_type"),
            source_value=source.get("section_type"),
        )

    return {k: v for k, v in props.items() if v is not None}
