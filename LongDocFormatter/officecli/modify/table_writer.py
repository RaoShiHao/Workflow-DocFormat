"""Write Word table format via officecli (1:1 with table_reader schema)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from LongDocFormatter.officecli.read.table_reader import (
    TableCellFormatInfo,
    TableFormatInfo,
    WordTableReader,
)
from LongDocFormatter.officecli.read.table_schema import (
    allow_break_to_cant_split,
    borders_to_officecli_props,
)

from ._cli import OfficeCliError, extract_officecli_warnings, set_properties
from ._format_props import assign_if_changed, resolve_bool_write
from .paragraph_writer import WordParagraphWriter
from .text_format_scope import text_format_for_migration


def _assign(props: dict[str, Any], key: str, target: Any, source: Any) -> None:
    assign_if_changed(props, key, target, source)


def table_format_to_officecli_props(
    table_format: dict[str, Any],
    *,
    source_table_format: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """
    ``table_format`` → officecli table props.

    Matches reader writable groups: ``layout``, ``borders``.
    ``style`` (``w:tblStyle`` id) is document-local and never written.
    ``pagination`` is applied via :meth:`WordTableWriter._apply_pagination`.
    """
    props: dict[str, Any] = {}
    warnings: list[str] = []
    source_table_format = source_table_format or {}

    layout = table_format.get("layout") or {}
    source_layout = source_table_format.get("layout") or {}
    _assign(props, "width", layout.get("width"), source_layout.get("width"))
    _assign(props, "align", layout.get("align"), source_layout.get("align"))
    _assign(props, "indent", layout.get("indent"), source_layout.get("indent"))

    props.update(
        borders_to_officecli_props(
            table_format.get("borders"),
            source_borders=source_table_format.get("borders"),
            target="table",
        )
    )
    return props, warnings


def row_format_to_officecli_props(
    row_format: dict[str, Any],
    *,
    source_row_format: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """
    ``row_format`` → officecli row props.

    Keys: ``height``, ``height_rule``, ``header``, ``allow_break_across_pages``.
    """
    props: dict[str, Any] = {}
    warnings: list[str] = []
    source_row_format = source_row_format or {}

    height = row_format.get("height")
    height_rule = row_format.get("height_rule")
    source_height = source_row_format.get("height")
    source_height_rule = source_row_format.get("height_rule")
    if height is not None:
        rule = str(height_rule or "").lower()
        if rule == "exact":
            _assign(props, "height.exact", height, source_height)
        else:
            _assign(props, "height", height, source_height)
    elif height_rule is not None and height_rule != source_height_rule:
        warnings.append("height_rule without height skipped.")

    header = row_format.get("header")
    if isinstance(header, bool):
        resolved = resolve_bool_write(
            target=header,
            source=source_row_format.get("header")
            if isinstance(source_row_format.get("header"), bool)
            else None,
        )
        if resolved is not None:
            props["header"] = resolved

    allow_break = row_format.get("allow_break_across_pages")
    source_allow_break = source_row_format.get("allow_break_across_pages")
    if allow_break is not None and allow_break != source_allow_break:
        props["cantSplit"] = allow_break_to_cant_split(bool(allow_break))

    return props, warnings


def cell_format_to_officecli_props(
    cell_format: dict[str, Any],
    *,
    source_cell_format: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """
    ``cell_format`` → officecli cell props (excludes paragraph ``text_format``).

    Writable groups: ``layout`` (``valign`` / ``align``), ``fill``, ``borders``.
    ``merge`` is read-only and intentionally omitted.
    """
    props: dict[str, Any] = {}
    warnings: list[str] = []
    source_cell_format = source_cell_format or {}

    layout = cell_format.get("layout") or {}
    source_layout = source_cell_format.get("layout") or {}
    _assign(props, "valign", layout.get("valign"), source_layout.get("valign"))
    _assign(props, "align", layout.get("align"), source_layout.get("align"))

    _assign(props, "fill", cell_format.get("fill"), source_cell_format.get("fill"))

    props.update(
        borders_to_officecli_props(
            cell_format.get("borders"),
            source_borders=source_cell_format.get("borders"),
        )
    )

    return props, warnings


@dataclass
class TableWriteResult:
    success: bool
    table_index: int
    path: str
    properties_set: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "table_index": self.table_index,
            "path": self.path,
            "properties_set": self.properties_set,
            "warnings": self.warnings,
            "error": self.error,
        }


@dataclass
class RowWriteResult:
    success: bool
    table_index: int
    row_index: int
    path: str
    properties_set: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "table_index": self.table_index,
            "row_index": self.row_index,
            "path": self.path,
            "properties_set": self.properties_set,
            "warnings": self.warnings,
            "error": self.error,
        }


@dataclass
class CellWriteResult:
    success: bool
    path: str
    row_index: int = 0
    col_index: int = 0
    properties_set: list[str] = field(default_factory=list)
    paragraph_results: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "path": self.path,
            "row_index": self.row_index,
            "col_index": self.col_index,
            "properties_set": self.properties_set,
            "paragraph_results": self.paragraph_results,
            "warnings": self.warnings,
            "error": self.error,
        }


@dataclass
class TableFullWriteResult:
    """Mirrors reader tree: table → rows → cells → paragraphs."""

    table: TableWriteResult
    rows: list[RowWriteResult] = field(default_factory=list)
    cells: list[CellWriteResult] = field(default_factory=list)

    @property
    def success(self) -> bool:
        if not self.table.success:
            return False
        if any(not r.success for r in self.rows):
            return False
        return all(c.success for c in self.cells)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "table": self.table.to_dict(),
            "rows": [r.to_dict() for r in self.rows],
            "cells": [c.to_dict() for c in self.cells],
        }


class WordTableWriter:
    """
    Apply format from :class:`~LongDocFormatter.officecli.read.table_reader.WordTableReader`.

    Write paths (mirror reader):

    - :meth:`apply_table_level` — ``table_format`` only
    - :meth:`apply_row_format` — one ``row_format``
    - :meth:`apply_cell_format` — one ``cell_format`` (no paragraphs)
    - :meth:`apply_cell` — ``cell_format`` + ``paragraphs[].text_format``
    - :meth:`apply_table` — full ``TableFormatInfo`` / ``to_dict()``
  """

    def __init__(self, doc_path: str | Path, officecli: str = "officecli") -> None:
        self.doc_path = Path(doc_path).resolve()
        self.officecli = officecli
        self._table_read_cache: dict[int, TableFormatInfo | None] = {}
        if not self.doc_path.is_file():
            raise FileNotFoundError(f"Document not found: {self.doc_path}")

    def _read_table_at_cached(self, table_index: int) -> TableFormatInfo | None:
        if table_index not in self._table_read_cache:
            self._table_read_cache[table_index] = WordTableReader(
                self.doc_path,
                officecli=self.officecli,
            ).read_at(table_index, merge_runs=False)
        return self._table_read_cache[table_index]

    def _table_path(self, table_index: int) -> str:
        return f"/body/tbl[{table_index}]"

    def _row_path(self, table_index: int, row_index: int) -> str:
        return f"/body/tbl[{table_index}]/tr[{row_index}]"

    def _cell_path(self, table_index: int, row_index: int, col_index: int) -> str:
        return f"/body/tbl[{table_index}]/tr[{row_index}]/tc[{col_index}]"

    def _read_table_level(self, table_index: int) -> TableFormatInfo | None:
        return WordTableReader(self.doc_path, officecli=self.officecli).read_table_level(
            table_index
        )

    def _source_row_format(self, table_index: int, row_index: int) -> dict[str, Any]:
        info = self._read_table_at_cached(table_index)
        if not info:
            return {}
        for row in info.rows:
            if int(row.row_index) == int(row_index):
                return dict(row.row_format or {})
        return {}

    def _source_cell_format(
        self,
        table_index: int,
        row_index: int,
        col_index: int,
    ) -> dict[str, Any]:
        info = self._read_table_at_cached(table_index)
        if not info:
            return {}
        for row in info.rows:
            if int(row.row_index) != int(row_index):
                continue
            for cell in row.cells:
                if int(cell.col_index) == int(col_index):
                    return dict(cell.cell_format or {})
        return {}

    def _set_at_path(
        self,
        path: str,
        props: dict[str, Any],
    ) -> tuple[bool, list[str], list[str]]:
        """Returns ``(ok, properties_set, warnings)``."""
        if not props:
            return True, [], []
        try:
            payload = set_properties(
                self.doc_path, path, props, officecli=self.officecli
            )
            warnings = extract_officecli_warnings(payload)
            return True, sorted(props.keys()), warnings
        except OfficeCliError:
            return False, sorted(props.keys()), []

    def _apply_pagination(
        self,
        table_index: int,
        pagination: dict[str, Any],
        rows: list[dict[str, Any]],
        *,
        source_rows: list[dict[str, Any]] | None = None,
    ) -> tuple[list[str], list[str]]:
        """
        Apply ``pagination.repeat_header`` / ``allow_break_across_pages``.

        Returns ``(extra_props_set, warnings)``.
        """
        warnings: list[str] = []
        extra: list[str] = []
        if not rows:
            return extra, warnings

        source_by_index = {
            int(row.get("row_index") or 0): row for row in (source_rows or [])
        }

        repeat_header = pagination.get("repeat_header")
        if repeat_header is True:
            row1 = rows[0]
            row_index = int(row1.get("row_index") or 1)
            source_row = source_by_index.get(row_index, {})
            source_row_format = dict(source_row.get("row_format") or {})
            if source_row_format.get("header") is not True:
                row_format = dict(row1.get("row_format") or {})
                row_format["header"] = True
                ok, keys, w = self._set_at_path(
                    self._row_path(table_index, row_index),
                    row_format_to_officecli_props(
                        row_format,
                        source_row_format=source_row_format,
                    )[0],
                )
                warnings.extend(w)
                if ok:
                    extra.extend(keys)

        allow_break = pagination.get("allow_break_across_pages")
        if allow_break is not None:
            cant_split = allow_break_to_cant_split(bool(allow_break))
            for row in rows:
                row_index = int(row.get("row_index") or 0)
                if row_index < 1:
                    continue
                row_format = row.get("row_format") or {}
                if row_format.get("allow_break_across_pages") is not None:
                    continue
                source_row_format = dict(
                    source_by_index.get(row_index, {}).get("row_format") or {}
                )
                source_allow = source_row_format.get("allow_break_across_pages")
                if source_allow is not None and bool(source_allow) == bool(allow_break):
                    continue
                ok, keys, w = self._set_at_path(
                    self._row_path(table_index, row_index),
                    {"cantSplit": cant_split},
                )
                warnings.extend(w)
                if ok:
                    extra.extend(keys)
        return extra, warnings

    def apply_table_level(
        self,
        table_index: int,
        table_format: dict[str, Any],
        *,
        rows: list[dict[str, Any]] | None = None,
    ) -> TableWriteResult:
        """Write ``table_format``; optional ``rows`` for ``pagination`` side effects."""
        path = self._table_path(table_index)
        source_info = self._read_table_level(table_index)
        source_table_format = (
            dict(source_info.table_format or {}) if source_info is not None else {}
        )
        source_rows = (
            [row.to_dict() for row in source_info.rows] if source_info is not None else []
        )
        props, warnings = table_format_to_officecli_props(
            table_format,
            source_table_format=source_table_format,
        )
        properties_set: list[str] = []

        if props:
            ok, keys, w = self._set_at_path(path, props)
            warnings.extend(w)
            if not ok:
                return TableWriteResult(
                    success=False,
                    table_index=table_index,
                    path=path,
                    properties_set=keys,
                    warnings=warnings,
                    error="officecli set failed on table.",
                )
            properties_set.extend(keys)

        pagination = table_format.get("pagination") or {}
        if rows and pagination:
            extra, pw = self._apply_pagination(
                table_index,
                pagination,
                rows,
                source_rows=source_rows,
            )
            warnings.extend(pw)
            properties_set.extend(extra)

        if not properties_set and not (rows and pagination):
            return TableWriteResult(
                success=False,
                table_index=table_index,
                path=path,
                warnings=warnings,
                error="No writable table_format properties (all null).",
            )

        return TableWriteResult(
            success=True,
            table_index=table_index,
            path=path,
            properties_set=sorted(set(properties_set)),
            warnings=warnings,
        )

    def apply_row_format(
        self,
        table_index: int,
        row_index: int,
        row_format: dict[str, Any],
    ) -> RowWriteResult:
        path = self._row_path(table_index, row_index)
        source_row_format = self._source_row_format(table_index, row_index)
        props, warnings = row_format_to_officecli_props(
            row_format,
            source_row_format=source_row_format,
        )
        if not props:
            return RowWriteResult(
                success=True,
                table_index=table_index,
                row_index=row_index,
                path=path,
                properties_set=[],
                warnings=warnings,
            )
        ok, keys, w = self._set_at_path(path, props)
        warnings.extend(w)
        return RowWriteResult(
            success=ok,
            table_index=table_index,
            row_index=row_index,
            path=path,
            properties_set=keys,
            warnings=warnings,
            error="" if ok else "officecli set failed on row.",
        )

    def apply_cell_format(
        self,
        table_index: int,
        row_index: int,
        col_index: int,
        cell_format: dict[str, Any],
        *,
        path: str | None = None,
    ) -> CellWriteResult:
        """Write ``cell_format`` only (no ``paragraphs`` / ``text_format``)."""
        cell_path = path or self._cell_path(table_index, row_index, col_index)
        source_cell_format = self._source_cell_format(table_index, row_index, col_index)
        props, warnings = cell_format_to_officecli_props(
            cell_format,
            source_cell_format=source_cell_format,
        )
        if not props:
            return CellWriteResult(
                success=True,
                path=cell_path,
                row_index=row_index,
                col_index=col_index,
                properties_set=[],
                warnings=warnings,
            )
        ok, keys, w = self._set_at_path(cell_path, props)
        warnings.extend(w)
        return CellWriteResult(
            success=ok,
            path=cell_path,
            row_index=row_index,
            col_index=col_index,
            properties_set=keys,
            warnings=warnings,
            error="" if ok else "officecli set failed on cell.",
        )

    def apply_cell(
        self,
        table_index: int,
        cell: TableCellFormatInfo | dict[str, Any],
        *,
        apply_paragraphs: bool = True,
    ) -> CellWriteResult:
        """``cell_format`` + optional ``paragraphs[].text_format`` (+ ``runs``)."""
        if isinstance(cell, dict):
            row_index = int(cell.get("row_index", 0))
            col_index = int(cell.get("col_index", 0))
            path = cell.get("path") or self._cell_path(
                table_index, row_index, col_index
            )
            cell_format = cell.get("cell_format") or {}
            paragraphs = cell.get("paragraphs") or []
        else:
            row_index = cell.row_index
            col_index = cell.col_index
            path = cell.path or self._cell_path(table_index, row_index, col_index)
            cell_format = cell.cell_format
            paragraphs = [p.to_dict() for p in cell.paragraphs]

        cell_result = self.apply_cell_format(
            table_index,
            row_index,
            col_index,
            cell_format,
            path=path,
        )
        if not apply_paragraphs or not paragraphs:
            return cell_result

        para_writer = WordParagraphWriter(self.doc_path, officecli=self.officecli)
        para_ok = True
        for para in paragraphs:
            para_path = para.get("path")
            text_format = para.get("text_format")
            if not para_path or not text_format:
                continue
            tf_result = para_writer.apply_text_format_at_path(
                para_path,
                text_format_for_migration(text_format),
                paragraph_text=para.get("text"),
            )
            cell_result.paragraph_results.append(tf_result.to_dict())
            cell_result.warnings.extend(
                tf_result.paragraph.warnings
                + (tf_result.runs.warnings if tf_result.runs else [])
            )
            if not tf_result.success:
                para_ok = False

        cell_result.success = cell_result.success and para_ok
        return cell_result

    def apply_table(
        self,
        table_index: int,
        source: TableFormatInfo | dict[str, Any],
        *,
        apply_cells: bool = True,
        apply_paragraphs: bool = True,
    ) -> TableFullWriteResult:
        """
        Apply full table JSON (same shape as :meth:`TableFormatInfo.to_dict`).

        Order: ``table_format`` → ``rows[].row_format`` → ``cells[].cell_format``
        → ``cells[].paragraphs[].text_format``.
        """
        if isinstance(source, dict):
            table_format = source.get("table_format") or {}
            rows = source.get("rows") or []
        else:
            table_format = source.table_format
            rows = [r.to_dict() for r in source.rows]

        table_result = self.apply_table_level(
            table_index, table_format, rows=rows
        )
        row_results: list[RowWriteResult] = []
        cell_results: list[CellWriteResult] = []

        for row in rows:
            row_index = int(row.get("row_index") or 0)
            row_format = row.get("row_format")
            if row_format and row_index > 0:
                row_res = self.apply_row_format(table_index, row_index, row_format)
                row_results.append(row_res)
                table_result.warnings.extend(row_res.warnings)

            if not apply_cells:
                continue
            for cell in row.get("cells") or []:
                cell_results.append(
                    self.apply_cell(
                        table_index,
                        cell,
                        apply_paragraphs=apply_paragraphs,
                    )
                )

        return TableFullWriteResult(
            table=table_result, rows=row_results, cells=cell_results
        )

    def apply_from_reader(
        self,
        source_doc: str | Path,
        source_table_index: int,
        target_table_index: int,
        *,
        table_level_only: bool = False,
        merge_runs: bool = True,
    ) -> TableFullWriteResult:
        """Read from ``source_doc``, write to ``self.doc_path``."""
        reader = WordTableReader(source_doc, officecli=self.officecli)
        if table_level_only:
            info = reader.read_table_level(source_table_index)
            if not info:
                failed = TableWriteResult(
                    success=False,
                    table_index=target_table_index,
                    path=self._table_path(target_table_index),
                    error=f"Source table {source_table_index} not found.",
                )
                return TableFullWriteResult(table=failed, rows=[], cells=[])
            return self.apply_table(
                target_table_index,
                info.to_dict(),
                apply_paragraphs=False,
            )

        info = reader.read_at(source_table_index, merge_runs=merge_runs)
        if not info:
            failed = TableWriteResult(
                success=False,
                table_index=target_table_index,
                path=self._table_path(target_table_index),
                error=f"Source table {source_table_index} not found.",
            )
            return TableFullWriteResult(table=failed, rows=[], cells=[])
        return self.apply_table(target_table_index, info)
