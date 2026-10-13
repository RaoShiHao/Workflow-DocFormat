"""Read Word table format via officecli (table-level + cell-level, flat schema)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ._cli import get_element, query_elements
from ._ooxml_table import (
    aggregate_allow_break_across_pages,
    cant_split_to_allow_break,
    read_row_cant_split_flags,
)
from .table_schema import enrich_table_borders_from_perimeter_cells, normalize_cell_fill, normalize_table_width
from .text_reader import WordTextReader, _build_text_format

_BORDER_SIDE_MAP = {
    "top": "top",
    "bottom": "bottom",
    "left": "left",
    "right": "right",
    "insideH": "horizontal",
    "insideV": "vertical",
    "all": "all",
}

# 三线表等：由 table/cell 的 border.top / border.bottom / border.horizontal /
# border.vertical 组合控制（officecli 在表级 fan-out 或单元格级设置）。


def _omit_none(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if v is not None}


def _pick(fmt: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in fmt and fmt[key] is not None:
            return fmt[key]
    return None


def _scalar_from_fmt(fmt: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in fmt:
            return fmt[key]
    return None


def _path_index(path: str, element: str) -> int | None:
    match = re.search(rf"/{element}\[(\d+)\]", path)
    return int(match.group(1)) if match else None


def _build_border_edges(fmt: dict[str, Any], prefix: str = "border") -> dict[str, Any]:
    edges: dict[str, Any] = {}
    for office_side, out_side in _BORDER_SIDE_MAP.items():
        key = f"{prefix}.{office_side}"
        style = fmt.get(key)
        if style is None and f"{key}.sz" not in fmt:
            edges[out_side] = None
            continue
        edges[out_side] = _omit_none(
            {"style": style, "size": fmt.get(f"{key}.sz")}
        ) or None
    return edges


def _build_table_layout(fmt: dict[str, Any]) -> dict[str, Any]:
    """Table-level layout (autofit scope: no col widths / fixed layout / spacing / direction)."""
    return {
        "width": normalize_table_width(_scalar_from_fmt(fmt, "width")),
        "align": _pick(fmt, "align", "alignment"),
        "indent": _scalar_from_fmt(fmt, "indent"),
    }


def _build_table_structure(
    fmt: dict[str, Any], *, row_count: int | None = None
) -> dict[str, Any]:
    """Structural readback only — not part of ``table_format`` migration."""
    rows = _scalar_from_fmt(fmt, "rows")
    cols = _scalar_from_fmt(fmt, "cols")
    return {
        "rows": int(rows) if rows is not None else row_count,
        "cols": int(cols) if cols is not None else None,
        "grid_cols": _scalar_from_fmt(fmt, "_gridCols"),
    }


def _build_table_pagination(
    row_formats: list[dict[str, Any]],
    *,
    cant_split_flags: list[bool] | None = None,
) -> dict[str, Any]:
    repeat_header = any(rf.get("header") is True for rf in row_formats)
    allow_break: bool | None = None
    if cant_split_flags is not None:
        allow_break = aggregate_allow_break_across_pages(cant_split_flags)
    else:
        row_values = [
            rf["allow_break_across_pages"]
            for rf in row_formats
            if rf.get("allow_break_across_pages") is not None
        ]
        if row_values and all(v == row_values[0] for v in row_values):
            allow_break = row_values[0]
    return {
        "repeat_header": repeat_header,
        "allow_break_across_pages": allow_break,
    }


def _build_table_format(
    fmt: dict[str, Any],
    row_formats: list[dict[str, Any]],
    *,
    cant_split_flags: list[bool] | None = None,
) -> dict[str, Any]:
    borders = _build_border_edges(fmt)
    return {
        "style": _pick(fmt, "style", "tableStyle", "tableStyleId"),
        "layout": _build_table_layout(fmt),
        "borders": borders,
        "pagination": _build_table_pagination(
            row_formats, cant_split_flags=cant_split_flags
        ),
    }


def _build_row_format(
    fmt: dict[str, Any],
    *,
    cant_split_on: bool | None = None,
) -> dict[str, Any]:
    allow_break: bool | None = None
    if cant_split_on is not None:
        allow_break = cant_split_to_allow_break(cant_split_on)
    elif "cantSplit" in fmt:
        raw = fmt.get("cantSplit")
        if isinstance(raw, bool):
            allow_break = cant_split_to_allow_break(raw)
        else:
            allow_break = cant_split_to_allow_break(
                str(raw).strip().lower() in {"true", "1", "on", "yes"}
            )
    return {
        "height": _scalar_from_fmt(fmt, "height"),
        "height_rule": _scalar_from_fmt(fmt, "height.rule", "heightRule"),
        "header": fmt.get("header") is True,
        "allow_break_across_pages": allow_break,
    }


def _build_cell_merge(fmt: dict[str, Any]) -> dict[str, Any]:
    """Merge structure (read-only; not written by table_writer)."""
    return {
        "colspan": _scalar_from_fmt(fmt, "colspan", "gridspan"),
        "vmerge": _scalar_from_fmt(fmt, "vmerge"),
        "hmerge": _scalar_from_fmt(fmt, "hmerge"),
    }


def _build_cell_layout(fmt: dict[str, Any]) -> dict[str, Any]:
    return {
        "valign": _scalar_from_fmt(fmt, "valign"),
        "align": _pick(fmt, "align", "alignment"),
    }


def _build_cell_format(fmt: dict[str, Any]) -> dict[str, Any]:
    return {
        "layout": _build_cell_layout(fmt),
        "merge": _build_cell_merge(fmt),
        "fill": normalize_cell_fill(fmt),
        "borders": _build_border_edges(fmt),
    }


def _build_paragraph_format(
    para_node: dict[str, Any],
    *,
    text_reader: WordTextReader,
    merge_runs: bool,
) -> dict[str, Any]:
    para_node = text_reader._load_runs_if_needed(para_node, merge_runs=merge_runs)
    para_fmt = dict(para_node.get("format") or {})
    run_nodes = para_node.get("children") or []
    return _build_text_format(
        para_fmt,
        node_style=para_node.get("style"),
        run_nodes=run_nodes,
        merge_runs=merge_runs,
        font_context=text_reader.get_font_context(),
    )


@dataclass
class TableParagraphInfo:
    path: str
    paragraph_index: int
    text: str
    text_format: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "paragraph_index": self.paragraph_index,
            "path": self.path,
            "text": self.text,
            "text_format": self.text_format,
        }


@dataclass
class TableCellFormatInfo:
    """Cell-level format (layout, borders, fill) + paragraph ``text_format``."""

    path: str
    row_index: int
    col_index: int
    cell_format: dict[str, Any] = field(default_factory=dict)
    paragraphs: list[TableParagraphInfo] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "row_index": self.row_index,
            "col_index": self.col_index,
            "path": self.path,
            "cell_format": self.cell_format,
            "paragraphs": [p.to_dict() for p in self.paragraphs],
        }


@dataclass
class TableRowFormatInfo:
    path: str
    row_index: int
    row_format: dict[str, Any] = field(default_factory=dict)
    cells: list[TableCellFormatInfo] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "row_index": self.row_index,
            "path": self.path,
            "row_format": self.row_format,
            "cells": [c.to_dict() for c in self.cells],
        }


@dataclass
class TableFormatInfo:
    """
    One table: **table-level** ``table_format`` plus optional row/cell tree.

    Use :meth:`WordTableReader.read_table_level` for table props only, or
    :meth:`WordTableReader.read_cells` for cell + paragraph formats.
    """

    table_index: int
    path: str
    table_format: dict[str, Any] = field(default_factory=dict)
    structure: dict[str, Any] = field(default_factory=dict)
    rows: list[TableRowFormatInfo] = field(default_factory=list)

    @property
    def row_count(self) -> int:
        return len(self.rows)

    def to_dict(self) -> dict[str, Any]:
        return {
            "table_index": self.table_index,
            "path": self.path,
            "row_count": self.row_count,
            "structure": self.structure,
            "table_format": self.table_format,
            "rows": [row.to_dict() for row in self.rows],
        }


class WordTableReader:
    """
    Read table formatting at two granularities:

    1. **Table-level** — width, alignment, indent, borders (三线表 via
       ``borders.top`` / ``borders.bottom`` / ``borders.horizontal`` / ``borders.vertical``)
    2. **Cell-level** — per-cell layout + each inner paragraph's ``text_format``

    Merged cells (``colspan`` / ``vmerge``) are exposed as read-only layout hints.
    Format migration uses ``path`` (``tc[i]/p[j]``), not logical column indices.
    """

    SELECTOR = "table"
    DEFAULT_DEPTH = 5

    def __init__(self, doc_path: str | Path, officecli: str = "officecli") -> None:
        self.doc_path = Path(doc_path).resolve()
        self.officecli = officecli
        if not self.doc_path.is_file():
            raise FileNotFoundError(f"Document not found: {self.doc_path}")

    def _text_reader(self) -> WordTextReader:
        return WordTextReader(self.doc_path, officecli=self.officecli)

    def _load_table_node(
        self,
        table_index: int,
        *,
        depth: int | None = None,
    ) -> dict[str, Any] | None:
        return get_element(
            self.doc_path,
            f"/body/tbl[{table_index}]",
            officecli=self.officecli,
            depth=depth if depth is not None else self.DEFAULT_DEPTH,
        )

    def _parse_rows(
        self,
        node: dict[str, Any],
        *,
        table_index: int,
        include_paragraphs: bool,
        merge_runs: bool,
    ) -> list[TableRowFormatInfo]:
        text_reader = self._text_reader() if include_paragraphs else None
        cant_split_flags = read_row_cant_split_flags(self.doc_path, table_index)
        rows: list[TableRowFormatInfo] = []
        for row_node in node.get("children") or []:
            if row_node.get("type") != "row":
                continue
            row_path = row_node.get("path", "")
            row_index = _path_index(row_path, "tr") or len(rows) + 1
            cs_on = (
                cant_split_flags[row_index - 1]
                if row_index - 1 < len(cant_split_flags)
                else None
            )
            row_fmt = _build_row_format(
                dict(row_node.get("format") or {}), cant_split_on=cs_on
            )
            cells: list[TableCellFormatInfo] = []

            for cell_node in row_node.get("children") or []:
                if cell_node.get("type") != "cell":
                    continue
                cell_path = cell_node.get("path", "")
                col_index = _path_index(cell_path, "tc") or len(cells) + 1
                cell_fmt = _build_cell_format(dict(cell_node.get("format") or {}))
                paragraphs: list[TableParagraphInfo] = []
                if include_paragraphs and text_reader is not None:
                    para_counter = 0
                    for child in cell_node.get("children") or []:
                        if child.get("type") != "paragraph":
                            continue
                        para_counter += 1
                        paragraphs.append(
                            TableParagraphInfo(
                                path=child.get("path", ""),
                                paragraph_index=para_counter,
                                text=(
                                    child.get("text") or child.get("preview") or ""
                                ).strip(),
                                text_format=_build_paragraph_format(
                                    child,
                                    text_reader=text_reader,
                                    merge_runs=merge_runs,
                                ),
                            )
                        )
                cells.append(
                    TableCellFormatInfo(
                        path=cell_path,
                        row_index=row_index,
                        col_index=col_index,
                        cell_format=cell_fmt,
                        paragraphs=paragraphs,
                    )
                )
            rows.append(
                TableRowFormatInfo(
                    path=row_path,
                    row_index=row_index,
                    row_format=row_fmt,
                    cells=cells,
                )
            )
        return rows

    def _node_to_table_info(
        self,
        node: dict[str, Any],
        *,
        table_index: int,
        include_paragraphs: bool = True,
        merge_runs: bool = True,
    ) -> TableFormatInfo:
        raw_fmt = dict(node.get("format") or {})
        rows = self._parse_rows(
            node,
            table_index=table_index,
            include_paragraphs=include_paragraphs,
            merge_runs=merge_runs,
        )
        row_fmts = [r.row_format for r in rows]
        cant_split_flags = read_row_cant_split_flags(self.doc_path, table_index)
        table_format = _build_table_format(
            raw_fmt, row_fmts, cant_split_flags=cant_split_flags
        )
        table_format = {
            **table_format,
            "borders": enrich_table_borders_from_perimeter_cells(table_format, rows),
        }
        return TableFormatInfo(
            table_index=table_index,
            path=node.get("path", ""),
            structure=_build_table_structure(raw_fmt, row_count=len(rows) or None),
            table_format=table_format,
            rows=rows,
        )

    def read_all(
        self,
        *,
        merge_runs: bool = True,
        include_paragraphs: bool = True,
    ) -> list[TableFormatInfo]:
        nodes = query_elements(
            self.doc_path,
            self.SELECTOR,
            officecli=self.officecli,
        )
        results: list[TableFormatInfo] = []
        for index, summary in enumerate(nodes, start=1):
            path = summary.get("path", "")
            if not path:
                continue
            detailed = get_element(
                self.doc_path,
                path,
                officecli=self.officecli,
                depth=self.DEFAULT_DEPTH,
            )
            if detailed:
                results.append(
                    self._node_to_table_info(
                        detailed,
                        table_index=index,
                        include_paragraphs=include_paragraphs,
                        merge_runs=merge_runs,
                    )
                )
        return results

    def read_at(
        self,
        table_index: int = 1,
        *,
        merge_runs: bool = True,
        include_paragraphs: bool = True,
        depth: int | None = None,
    ) -> TableFormatInfo | None:
        node = self._load_table_node(table_index, depth=depth)
        if not node:
            return None
        return self._node_to_table_info(
            node,
            table_index=table_index,
            include_paragraphs=include_paragraphs,
            merge_runs=merge_runs,
        )

    def read_table_level(self, table_index: int = 1) -> TableFormatInfo | None:
        """Table + row + cell layout only (no paragraph ``text_format``)."""
        return self.read_at(table_index, include_paragraphs=False)

    def read_cells(
        self,
        table_index: int = 1,
        *,
        merge_runs: bool = True,
    ) -> list[TableCellFormatInfo]:
        """Flat list of cells with ``cell_format`` and paragraph ``text_format``."""
        info = self.read_at(table_index, merge_runs=merge_runs, include_paragraphs=True)
        if not info:
            return []
        cells: list[TableCellFormatInfo] = []
        for row in info.rows:
            cells.extend(row.cells)
        return cells

    def read_cell_at(
        self,
        table_index: int,
        row_index: int,
        cell_index: int,
        *,
        merge_runs: bool = True,
        depth: int | None = None,
    ) -> TableCellFormatInfo | None:
        node = get_element(
            self.doc_path,
            f"/body/tbl[{table_index}]/tr[{row_index}]/tc[{cell_index}]",
            officecli=self.officecli,
            depth=depth if depth is not None else self.DEFAULT_DEPTH,
        )
        if not node:
            return None
        text_reader = self._text_reader()
        cell_fmt = _build_cell_format(dict(node.get("format") or {}))
        paragraphs: list[TableParagraphInfo] = []
        para_counter = 0
        for child in node.get("children") or []:
            if child.get("type") != "paragraph":
                continue
            para_counter += 1
            paragraphs.append(
                TableParagraphInfo(
                    path=child.get("path", ""),
                    paragraph_index=para_counter,
                    text=(child.get("text") or child.get("preview") or "").strip(),
                    text_format=_build_paragraph_format(
                        child,
                        text_reader=text_reader,
                        merge_runs=merge_runs,
                    ),
                )
            )
        return TableCellFormatInfo(
            path=node.get("path", ""),
            row_index=row_index,
            col_index=cell_index,
            cell_format=cell_fmt,
            paragraphs=paragraphs,
        )
