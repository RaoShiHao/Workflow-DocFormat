"""Page/section format field builders (flat groups, migration null semantics)."""

from __future__ import annotations

from typing import Any

from .format_schema import _scalar_from_fmt

COLUMN_KEYS = (
    "count",
    "spacing",
    "equal_width",
    "separator",
    "col_widths",
    "first_width",
)
GRID_KEYS = (
    "layout_mode",
    "line_pitch",
    "char_space",
)
PAGE_NUMBER_KEYS = (
    "format",
    "alignment",
    "start",
    "continue",
    "name",
    "name_ascii",
    "name_far_east",
    "size",
)

# COM wdLineStyle (page_reader_config) ↔ officecli pbdr.bottom style token
_BORDER_STYLE_TO_LINE: dict[str, int] = {
    "none": 0,
    "single": 1,
    "dotted": 2,
    "dot": 2,
    "dashsmallgap": 3,
    "dashed": 4,
    "dashlargegap": 4,
    "dashdot": 5,
    "dashdotdot": 6,
    "double": 7,
    "triple": 8,
}
_LINE_TO_BORDER_STYLE: dict[int, str] = {
    0: "none",
    1: "single",
    2: "dotted",
    3: "dashSmallGap",
    4: "dashed",
    5: "dashDot",
    6: "dashDotDot",
    7: "double",
    8: "triple",
}


def _pick_fmt(fmt: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in fmt and fmt[key] is not None:
            return fmt[key]
    return None


def parse_border_line(fmt: dict[str, Any]) -> int | None:
    """Read header bottom border as COM ``border_line`` int (``null`` = unset/none)."""
    raw = _pick_fmt(fmt, "pbdr.bottom", "pBdr.bottom")
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text.lower() in {"none", "0"}:
        return 0
    style_token = text.split(";", 1)[0].strip().lower().replace("_", "")
    return _BORDER_STYLE_TO_LINE.get(style_token)


def border_line_to_pbdr_bottom(border_line: Any) -> str | None:
    """Write header bottom border via officecli ``pbdr.bottom``."""
    if border_line is None:
        return None
    try:
        code = int(border_line)
    except (TypeError, ValueError):
        return None
    style = _LINE_TO_BORDER_STYLE.get(code, "none")
    if code == 0:
        return "none"
    return f"{style};4;auto;0"


def page_start_fields(page_start: Any) -> tuple[bool | None, Any]:
    """
    Derive ``(continue, start)`` for footer ``page_number`` from section ``pageStart``.

    ``continue=true`` when numbering continues from the previous section.
    """
    if page_start is None:
        return None, None
    text = str(page_start).strip().lower()
    if text in {"", "none"}:
        return True, None
    return False, page_start


def build_page_number(
    section_fmt: dict[str, Any],
    merged_fmt: dict[str, Any],
) -> dict[str, Any]:
    """Footer slot page-number settings (aligned with ``page_reader_config``)."""
    page_num_fmt = _scalar_from_fmt(section_fmt, "pageNumFmt")
    if page_num_fmt is not None and str(page_num_fmt).strip().lower() in {"", "none"}:
        page_num_fmt = None
    continue_num, start = page_start_fields(_scalar_from_fmt(section_fmt, "pageStart"))
    alignment = _pick_fmt(merged_fmt, "align", "alignment", "effective.alignment")
    if alignment is not None:
        alignment = str(alignment).lower()

    fonts = _font_triplet_from_fmt(merged_fmt)
    size = _pick_fmt(merged_fmt, "size", "effective.size")
    return _omit_none_dict(
        {
            "format": page_num_fmt,
            "alignment": alignment,
            "start": start,
            "continue": continue_num,
            **fonts,
            "size": str(size) if size is not None else None,
        }
    )


def enrich_page_number_defaults(
    page_number: dict[str, Any],
    *,
    section_index: int,
    section_count: int,
    page_start_raw: Any = None,
) -> dict[str, Any]:
    """
    Default ``start`` / ``continue`` / ``format`` when OOXML omits ``pageStart``.

    Mirrors Path B (``TemplateAgent`` COM ``footer.PageNumbers``) semantics:

    - ``StartingNumber`` defaults to ``1``
    - ``RestartNumberingAtSection=True`` → ``continue=False`` (restart at this section)
    - Single-section documents with a PAGE field use restart-at-1 (gov_doc-style)
    - Later sections without ``pageStart`` continue numbering from the prior section
    """
    if page_start_raw is not None:
        return dict(page_number)

    out = dict(page_number)
    if out.get("start") is not None and out.get("continue") is not None:
        return out

    if section_count <= 1:
        out.setdefault("start", 1)
        out.setdefault("continue", False)
    elif section_index <= 1:
        out.setdefault("start", 1)
        out.setdefault("continue", False)
    else:
        out.setdefault("continue", True)

    if out.get("format") is None and (
        out.get("start") is not None or out.get("continue") is not None
    ):
        out.setdefault("format", "decimal")

    return _omit_none_dict(out)


def resolve_section_page_start(
    section_fmt: dict[str, Any],
    footer: dict[str, Any] | None,
) -> Any:
    """Top-level ``page_start`` from OOXML or enriched footer ``page_number``."""
    raw = section_fmt.get("pageStart")
    if raw is not None:
        return raw
    overrides = page_number_section_overrides(
        {
            "footer": footer or {},
            "page_num_fmt": section_fmt.get("pageNumFmt"),
        }
    )
    return overrides.get("page_start")


def _font_triplet_from_fmt(fmt: dict[str, Any]) -> dict[str, str]:
    return _omit_none_dict(
        {
            "name": _pick_fmt(fmt, "font", "font.latin", "effective.font.ascii"),
            "name_ascii": _pick_fmt(
                fmt, "font.latin", "font.ascii", "effective.font.ascii"
            ),
            "name_far_east": _pick_fmt(
                fmt, "font.ea", "font.eastAsia", "effective.font.eastAsia"
            ),
        }
    )


def _omit_none_dict(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if v is not None}


def page_number_from_footer_slot(slot: dict[str, Any] | None) -> dict[str, Any]:
    if not slot:
        return {}
    return dict(slot.get("page_number") or {})


def page_number_section_overrides(page_format: dict[str, Any]) -> dict[str, Any]:
    """
    Resolve section-level page numbering for write.

    Top-level ``page_num_fmt`` / ``page_start`` take precedence; otherwise use
    ``footer.*.page_number`` (``primary`` first).

    When a footer slot has page-number settings but no explicit ``format``, Word
    defaults to decimal — emit ``page_num_fmt=decimal`` so prior section formats
    (e.g. ``lowerRoman`` on content) are cleared during migration.
    """
    footer = page_format.get("footer") or {}
    page_number: dict[str, Any] = {}
    for slot_name in ("primary", "first", "even"):
        candidate = page_number_from_footer_slot(footer.get(slot_name))
        if candidate:
            page_number = candidate
            if slot_name == "primary":
                break

    result: dict[str, Any] = {}
    if "page_num_fmt" in page_format:
        result["page_num_fmt"] = page_format.get("page_num_fmt")
    elif page_number.get("format") is not None:
        result["page_num_fmt"] = page_number.get("format")
    elif page_number:
        result["page_num_fmt"] = "decimal"

    if "page_start" in page_format:
        result["page_start"] = page_format.get("page_start")
    elif "continue" in page_number or "start" in page_number:
        if page_number.get("continue") is True:
            result["page_start"] = None
        elif page_number.get("start") is not None:
            result["page_start"] = page_number.get("start")
    return result

_GRID_TYPE_TO_LAYOUT = {
    "default": "none",
    "none": "none",
    "lines": "lines_only",
    "linesandchars": "lines_and_chars",
    "snaptochars": "snap_to_chars",
}


def resolve_header_footer_refs(
    section_fmt: dict[str, Any],
    prefix: str,
) -> dict[str, str | None]:
    """Internal officecli paths for header/footer slots (writer use only)."""
    return {
        "primary": section_fmt.get(f"{prefix}Ref.default")
        or section_fmt.get(f"{prefix}Ref"),
        "first": section_fmt.get(f"{prefix}Ref.first"),
        "even": section_fmt.get(f"{prefix}Ref.even"),
    }


def build_columns(section_fmt: dict[str, Any]) -> dict[str, Any]:
    col_widths = _scalar_from_fmt(section_fmt, "colWidths")
    if col_widths is not None and str(col_widths).strip().lower() in {"", "none"}:
        col_widths = None
    first_width = None
    if isinstance(col_widths, str) and col_widths.strip():
        first_width = col_widths.split(",")[0].strip()
    spacing = _scalar_from_fmt(section_fmt, "columnSpace")
    if spacing is not None and str(spacing).strip().lower() in {"0", "0cm", "0pt"}:
        spacing = None
    return {
        "count": _scalar_from_fmt(section_fmt, "columns"),
        "spacing": spacing,
        "equal_width": _scalar_from_fmt(section_fmt, "columns.equalWidth"),
        "separator": _scalar_from_fmt(section_fmt, "columns.separator"),
        "col_widths": col_widths,
        "first_width": first_width,
    }


def _layout_mode_from_type(grid_type: Any) -> str | None:
    if grid_type is None:
        return None
    return _GRID_TYPE_TO_LAYOUT.get(str(grid_type).replace("_", "").lower())


def _omit_none_dict(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if v is not None}


def sanitize_grid_for_layout(grid: dict[str, Any] | None) -> dict[str, Any]:
    """
    Keep only grid fields meaningful for ``layout_mode`` (COM / TemplateAgent order).

    - ``none``: layout only — no pitch fields
    - ``lines_only``: layout + ``line_pitch`` only
    - ``lines_and_chars`` / ``snap_to_chars``: layout + both pitches when set
    """
    if not grid:
        return {}
    layout_mode = grid.get("layout_mode")
    if layout_mode in (None, "none"):
        return {"layout_mode": "none"}
    if layout_mode == "lines_only":
        return _omit_none_dict(
            {
                "layout_mode": layout_mode,
                "line_pitch": grid.get("line_pitch"),
            }
        )
    if layout_mode in ("lines_and_chars", "snap_to_chars"):
        return _omit_none_dict(
            {
                "layout_mode": layout_mode,
                "line_pitch": grid.get("line_pitch"),
                "char_space": grid.get("char_space"),
            }
        )
    return _omit_none_dict(dict(grid))


def build_grid(section_fmt: dict[str, Any]) -> dict[str, Any]:
    """
    Document grid (COM-aligned).

    ``docGrid.type`` is authoritative (like COM ``LayoutMode``). Orphan
    ``line_pitch`` / ``char_space`` without a grid type are stale and ignored.
  """
    raw_type = _scalar_from_fmt(section_fmt, "docGrid.type")
    line_pitch = _scalar_from_fmt(section_fmt, "docGrid.linePitch")
    char_space = _scalar_from_fmt(section_fmt, "docGrid.charSpace")

    layout_mode = _layout_mode_from_type(raw_type)
    if layout_mode is None:
        layout_mode = "none"

    if layout_mode == "none":
        return {"layout_mode": "none"}
    if layout_mode == "lines_only":
        return _omit_none_dict(
            {
                "layout_mode": "lines_only",
                "line_pitch": line_pitch,
            }
        )
    if layout_mode == "lines_and_chars":
        return _omit_none_dict(
            {
                "layout_mode": "lines_and_chars",
                "line_pitch": line_pitch,
                "char_space": char_space,
            }
        )
    return _omit_none_dict(
        {
            "layout_mode": layout_mode,
            "line_pitch": line_pitch,
            "char_space": char_space,
        }
    )
