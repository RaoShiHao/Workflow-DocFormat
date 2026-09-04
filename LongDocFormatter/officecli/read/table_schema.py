"""Table format schema shared by table_reader and table_writer."""

from __future__ import annotations

from typing import Any, Literal

BORDER_SIDE_TO_OFFICE: dict[str, str] = {
    "top": "border.top",
    "bottom": "border.bottom",
    "left": "border.left",
    "right": "border.right",
    "horizontal": "border.horizontal",
    "vertical": "border.vertical",
    "all": "border.all",
}

# Table tblPr uses insideH/insideV (reader maps these to horizontal/vertical).
TABLE_BORDER_SIDE_TO_OFFICE: dict[str, str] = {
    **BORDER_SIDE_TO_OFFICE,
    "horizontal": "border.insideH",
    "vertical": "border.insideV",
}

# Cell-level inner grid keys are not writable via officecli set_properties.
BORDER_SIDES_WRITE_SKIP = frozenset({"horizontal", "vertical"})

BorderWriteTarget = Literal["table", "cell"]

# Cell-level outer edges only (not table insideH/insideV).
CELL_WRITE_BORDER_SIDES = ("top", "bottom", "left", "right")

# Table-level edges including inner grid (insideH/insideV).
TABLE_WRITE_BORDER_SIDES = ("top", "bottom", "left", "right", "horizontal", "vertical")

# Template omitted a side → treat as borderless on that edge (write ``none``).
_TEMPLATE_BORDER_UNSET = object()

# Cell borders may inherit outer edges only (not table-level inner grid keys).
CELL_INHERITABLE_BORDER_SIDES = ("top", "bottom", "left", "right", "all")


def _template_cell_border_edge(borders: dict[str, Any], side: str) -> Any:
    if side in borders:
        return borders[side]
    if "all" in borders:
        return borders["all"]
    return _TEMPLATE_BORDER_UNSET


def borders_for_cell_inherit(borders: dict[str, Any] | None) -> dict[str, Any]:
    """Drop table-only inner grid keys before merging table borders into a cell."""
    if not borders:
        return {}
    return {
        side: borders[side]
        for side in CELL_INHERITABLE_BORDER_SIDES
        if side in borders
    }


def border_edge_to_officecli(edge: dict[str, Any] | None) -> str | None:
    """``{style, size}`` → officecli ``STYLE;SIZE`` or ``none``."""
    if edge is None:
        return None
    style = edge.get("style")
    size = edge.get("size")
    if style is None and size is None:
        return None
    if style is not None and str(style).lower() == "none":
        return "none"
    parts: list[str] = [str(style or "single")]
    if size is not None:
        parts.append(str(size))
    return ";".join(parts)


def border_edge_is_explicit(edge: Any) -> bool:
    """True when the template defines a concrete border edge (including explicit ``none``)."""
    if edge is None:
        return False
    if not isinstance(edge, dict):
        return True
    style = edge.get("style")
    size = edge.get("size")
    return style is not None or size is not None


def borders_have_explicit_edges(
    borders: dict[str, Any] | None,
    *,
    sides: tuple[str, ...] = TABLE_WRITE_BORDER_SIDES,
) -> bool:
    """Unset / inherit (reader ``null`` on every side) → False; do not write border props."""
    borders = borders or {}
    if border_edge_is_explicit(borders.get("all")):
        return True
    return any(border_edge_is_explicit(borders.get(side)) for side in sides)


def compact_borders_for_migration(
    borders: dict[str, Any] | None,
    *,
    target: BorderWriteTarget = "cell",
) -> dict[str, Any]:
    """
    Normalize borders for ``Style.format_config`` / migration write.

    Returns ``{}`` when every side inherits (table style / defaults). Keeps only
    explicitly defined edges so extract and modify agree on inherit vs borderless.
    """
    borders = borders or {}
    sides = TABLE_WRITE_BORDER_SIDES if target == "table" else CELL_WRITE_BORDER_SIDES
    if not borders_have_explicit_edges(borders, sides=sides):
        return {}
    out: dict[str, Any] = {}
    if border_edge_is_explicit(borders.get("all")):
        out["all"] = borders["all"]
    for side in sides:
        edge = borders.get(side)
        if border_edge_is_explicit(edge):
            out[side] = edge
    return out


def normalize_table_format_for_write(table_format: dict[str, Any] | None) -> dict[str, Any]:
    """Table ``format_config.table_format`` with compact inherit-aware ``borders``."""
    tf = dict(table_format or {})
    tf.pop("style", None)  # document-local tblStyle id — never migrate
    tf["borders"] = compact_borders_for_migration(tf.get("borders"), target="table")
    return tf


def _cell_border_edge(cell: Any, side: str) -> Any:
    if isinstance(cell, dict):
        borders = (cell.get("cell_format") or {}).get("borders") or {}
    else:
        borders = (getattr(cell, "cell_format", None) or {}).get("borders") or {}
    return borders.get(side)


def _uniform_explicit_edge(edges: list[Any]) -> dict[str, Any] | None:
    explicit = [edge for edge in edges if border_edge_is_explicit(edge)]
    if not explicit or len(explicit) != len(edges):
        return None
    signature = border_edge_to_officecli(explicit[0])
    if signature is None:
        return None
    for edge in explicit[1:]:
        if border_edge_to_officecli(edge) != signature:
            return None
    return dict(explicit[0])


def enrich_table_borders_from_perimeter_cells(
    table_format: dict[str, Any],
    rows: list[Any],
) -> dict[str, Any]:
    """
    Merge perimeter cell borders into ``table_format.borders`` when tblPr omits them.

    Word often stores the outer frame on edge cells (e.g. row-1 ``top``) while tblPr
    only has inside grid overrides. This promotes uniform edge-cell borders to the
    corresponding table-level side so ``table_structure`` migration can write them.
    """
    borders = dict(table_format.get("borders") or {})
    if not rows:
        return borders

    def row_cells(row: Any) -> list[Any]:
        if isinstance(row, dict):
            return list(row.get("cells") or [])
        return list(getattr(row, "cells", None) or [])

    first_row = row_cells(rows[0])
    last_row = row_cells(rows[-1])
    if not first_row:
        return borders

    if not border_edge_is_explicit(borders.get("top")):
        top_edge = _uniform_explicit_edge(
            [_cell_border_edge(cell, "top") for cell in first_row]
        )
        if top_edge is not None:
            borders["top"] = top_edge

    if not border_edge_is_explicit(borders.get("bottom")) and last_row:
        bottom_edge = _uniform_explicit_edge(
            [_cell_border_edge(cell, "bottom") for cell in last_row]
        )
        if bottom_edge is not None:
            borders["bottom"] = bottom_edge

    if not border_edge_is_explicit(borders.get("left")):
        left_edge = _uniform_explicit_edge(
            [_cell_border_edge(row_cells(row)[0], "left") for row in rows if row_cells(row)]
        )
        if left_edge is not None:
            borders["left"] = left_edge

    if not border_edge_is_explicit(borders.get("right")):
        right_edge = _uniform_explicit_edge(
            [_cell_border_edge(row_cells(row)[-1], "right") for row in rows if row_cells(row)]
        )
        if right_edge is not None:
            borders["right"] = right_edge

    return borders


def table_borders_to_officecli_props(
    borders: dict[str, Any] | None,
    *,
    source_borders: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Table ``borders`` → officecli props (tblPr).

    Only sides **explicitly present** in ``borders`` are written. Unspecified sides
    inherit (table style / defaults) and are left untouched on the target document.
    """
    borders = borders if borders is not None else {}
    source_borders = source_borders or {}
    if not borders_have_explicit_edges(borders, sides=TABLE_WRITE_BORDER_SIDES):
        return {}
    props: dict[str, Any] = {}

    for side in TABLE_WRITE_BORDER_SIDES:
        if side in borders and border_edge_is_explicit(borders.get(side)):
            template_edge = borders[side]
        elif border_edge_is_explicit(borders.get("all")):
            template_edge = borders["all"]
        else:
            continue

        target_value = border_edge_to_officecli(template_edge)
        if target_value is None:
            continue

        office_key = TABLE_BORDER_SIDE_TO_OFFICE[side]
        source_value = border_edge_to_officecli(source_borders.get(side))
        if target_value != source_value:
            props[office_key] = target_value

    return props


def cell_borders_to_officecli_props(
    borders: dict[str, Any] | None,
    *,
    source_borders: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Cell ``borders`` → officecli props.

    Only sides **explicitly present** in ``borders`` are written. Unspecified sides
    inherit (table / cell style) and are left untouched on the target cell.
    """
    borders = borders or {}
    source_borders = source_borders or {}
    if not borders_have_explicit_edges(borders, sides=CELL_WRITE_BORDER_SIDES):
        return {}
    props: dict[str, Any] = {}

    for side in CELL_WRITE_BORDER_SIDES:
        if side in borders and border_edge_is_explicit(borders.get(side)):
            template_edge = borders[side]
        elif border_edge_is_explicit(borders.get("all")):
            template_edge = borders["all"]
        else:
            continue

        target_value = border_edge_to_officecli(template_edge)
        if target_value is None:
            continue

        office_key = BORDER_SIDE_TO_OFFICE[side]
        source_value = border_edge_to_officecli(source_borders.get(side))
        if target_value != source_value:
            props[office_key] = target_value

    return props


def borders_to_officecli_props(
    borders: dict[str, Any] | None,
    *,
    source_borders: dict[str, Any] | None = None,
    target: BorderWriteTarget = "cell",
) -> dict[str, Any]:
    if target == "cell":
        return cell_borders_to_officecli_props(
            borders,
            source_borders=source_borders,
        )

    return table_borders_to_officecli_props(
        borders,
        source_borders=source_borders,
    )


def allow_break_to_cant_split(allow_break: bool) -> bool:
    return not allow_break


_AUTO_WIDTH_TOKENS = frozenset({"", "auto", "none", "null"})

# Merge structure — read for awareness; never written by table_writer (format-only scope).
CELL_STRUCTURE_LAYOUT_KEYS = frozenset({"colspan", "vmerge", "hmerge"})

BORDER_EDGE_KEYS = (
    "top",
    "bottom",
    "left",
    "right",
    "horizontal",
    "vertical",
    "all",
)


def normalize_table_width(raw: Any) -> str | None:
    """
    Map officecli ``width`` to an explicit migration value, or ``None``.

    Autofit / unset widths (``auto``, COM-style sentinels) become ``None`` so we
    do not treat a placeholder as a concrete dimension. Percent and twips
    strings from OOXML are kept as-is (e.g. ``100%``, ``5000``).
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        if raw < 0:
            return None
        text = str(int(raw)) if isinstance(raw, float) and raw.is_integer() else str(raw)
    else:
        text = str(raw).strip()
    if not text:
        return None
    lowered = text.lower()
    if lowered in _AUTO_WIDTH_TOKENS:
        return None
    if lowered.endswith("%"):
        return text
    if text.lstrip("-").isdigit():
        if int(text) < 0:
            return None
        return text
    if lowered == "auto":
        return None
    return text


def _scalar_from_fmt(fmt: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in fmt:
            return fmt[key]
    return None


def normalize_cell_fill(fmt: dict[str, Any]) -> str | None:
    """Map officecli cell shading to a hex fill string (e.g. ``#D9E2F3``)."""
    for key in ("fill", "background", "shading"):
        raw = fmt.get(key)
        if raw is not None and str(raw).strip():
            return str(raw).strip()
    shading_fill = _scalar_from_fmt(fmt, "shading.fill")
    if shading_fill is not None and str(shading_fill).strip():
        text = str(shading_fill).strip()
        return text if text.startswith("#") else f"#{text}"
    return None
