"""Write paragraph text_format and runs to a .docx via officecli (schema matches text_reader)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tools.migration.runs.run_target_resolver import resolve_run_target_match
from LongDocFormatter.officecli.read.text_reader import WordTextReader, _paragraph_level_base_effects

from ._cli import (
    OfficeCliError,
    extract_officecli_warnings,
    get_element,
    matched_count,
    set_properties,
    set_with_find,
)
from .numbering_mapper import NumberingMapper
from ._format_props import (
    augment_font_props_for_explicit_font,
    font_format_to_officecli_props,
    mark_rpr_typography_font,
    migration_uniform_run_font_props,
    needs_uniform_run_font_migration,
    resolve_bool_write,
    run_entry_to_officecli_props,
    typography_base_font,
)
from .text_format_scope import text_format_for_migration

_LINE_RULE_TO_OFFICECLI: dict[str, str] = {
    "single": "auto",
    "auto": "auto",
    "exact": "exact",
    "atLeast": "atLeast",
}

_INDENT_KEYS = (
    "left",
    "right",
    "first_line",
    "hanging",
    "first_line_chars",
    "hanging_chars",
)

DEFAULT_SINGLE_LINE_SPACING = "1x"


def _indent_is_unset(indent: dict[str, Any] | None) -> bool:
    if not indent:
        return True
    return all(indent.get(key) is None for key in _INDENT_KEYS)


def _apply_spacing_to_props(
    props: dict[str, Any],
    spacing: dict[str, Any],
    *,
    source_spacing: dict[str, Any] | None = None,
    source_office_format: dict[str, Any] | None = None,
) -> None:
    spacing = spacing or {}
    for src_key, office_key in (
        ("before", "spaceBefore"),
        ("after", "spaceAfter"),
    ):
        val = spacing.get(src_key)
        if val is not None:
            props[office_key] = val

    rule = spacing.get("line_spacing_rule")
    line_val = spacing.get("line_spacing")

    if rule == "single":
        props["lineRule"] = "auto"
        props["lineSpacing"] = line_val or DEFAULT_SINGLE_LINE_SPACING
        return

    if rule is not None:
        props["lineRule"] = _LINE_RULE_TO_OFFICECLI.get(str(rule), rule)
    if line_val is not None:
        props["lineSpacing"] = line_val


def _apply_indent_to_props(
    props: dict[str, Any],
    indent: dict[str, Any],
    *,
    source_office_format: dict[str, Any] | None = None,
) -> None:
    indent = indent or {}
    source_office = source_office_format or {}

    if _indent_is_unset(indent):
        clears: dict[str, Any] = {}
        if source_office.get("indent") is not None or source_office.get("leftIndent") is not None:
            clears["indent"] = "0pt"
        if source_office.get("rightIndent") is not None:
            clears["rightIndent"] = "0pt"
        if source_office.get("firstLineIndent") is not None:
            clears["firstLineIndent"] = "0pt"
        if source_office.get("hangingIndent") is not None:
            clears["hangingIndent"] = "0pt"
        if source_office.get("firstLineChars") is not None:
            clears["firstLineChars"] = 0
        if source_office.get("hangingChars") is not None:
            clears["hangingChars"] = 0
        props.update(clears)
        return

    if indent.get("left") is not None:
        props["indent"] = indent["left"]
    if indent.get("right") is not None:
        props["rightIndent"] = indent["right"]
    if indent.get("first_line") is not None:
        props["firstLineIndent"] = indent["first_line"]
    if indent.get("hanging") is not None:
        props["hangingIndent"] = indent["hanging"]
    if indent.get("first_line_chars") is not None:
        props["firstLineChars"] = indent["first_line_chars"]
    if indent.get("hanging_chars") is not None:
        props["hangingChars"] = indent["hanging_chars"]


class ParagraphNotFoundError(Exception):
    """No body paragraph exists for the requested 1-based index."""


@dataclass
class RunApplyResult:
    """Result of applying one ``runs[]`` entry via ``--find``."""

    text: str
    matched: int
    properties_set: list[str] = field(default_factory=list)
    success: bool = False
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "matched": self.matched,
            "properties_set": self.properties_set,
            "success": self.success,
            "error": self.error,
        }


@dataclass
class ParagraphWriteResult:
    """Result of applying paragraph-level ``text_format`` (excluding ``runs``)."""

    success: bool
    paragraph_index: int
    path: str
    properties_set: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "paragraph_index": self.paragraph_index,
            "path": self.path,
            "properties_set": self.properties_set,
            "warnings": self.warnings,
            "error": self.error,
        }


@dataclass
class ParagraphRunsWriteResult:
    """Result of applying ``runs[]`` with ``--find`` inside one paragraph."""

    success: bool
    paragraph_index: int
    path: str
    run_results: list[RunApplyResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "paragraph_index": self.paragraph_index,
            "path": self.path,
            "run_results": [r.to_dict() for r in self.run_results],
            "warnings": self.warnings,
            "error": self.error,
        }


@dataclass
class TextFormatWriteResult:
    """Paragraph format + optional ``runs`` applied together."""

    paragraph: ParagraphWriteResult
    runs: ParagraphRunsWriteResult | None = None

    @property
    def success(self) -> bool:
        if self.paragraph and not self.paragraph.success:
            return False
        if self.runs and not self.runs.success:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "paragraph": self.paragraph.to_dict(),
            "runs": self.runs.to_dict() if self.runs else None,
        }


def text_format_to_officecli_props(
    text_format: dict[str, Any],
    *,
    source_text_format: dict[str, Any] | None = None,
    source_office_format: dict[str, Any] | None = None,
    include_style_name: bool = False,
) -> tuple[dict[str, Any], list[str]]:
    """
    Map flat ``text_format`` (from :class:`WordTextReader`) to paragraph ``officecli set`` props.

    ``runs`` are excluded; use :meth:`WordParagraphWriter.apply_runs` or ``apply_text_format``.

    When ``source_text_format`` is provided (current document state at the target path),
    boolean ``false`` values are only written to clear an effect that was previously on.

    By default ``style_name`` is **not** written (``include_style_name=False``) so migration
    does not reset theme-linked fonts via ``styleName``.
    """
    props: dict[str, Any] = {}
    warnings: list[str] = []

    if not text_format:
        return props, warnings

    if text_format.get("runs"):
        warnings.append(
            "``runs`` skipped here; apply via apply_runs / apply_text_format."
        )

    source = source_text_format or {}
    mark_rpr = text_format.get("mark_rpr_font")
    if mark_rpr:
        mark_typography = mark_rpr_typography_font(mark_rpr)
        mark_props, mark_run_scope, mark_warnings = font_format_to_officecli_props(
            mark_typography,
            None,
            source_base_font=mark_rpr_typography_font(source.get("mark_rpr_font") or {}),
            source_office_format=source_office_format,
            scope="paragraph",
            allow_typography=True,
        )
        props.update(mark_props)
        warnings.extend(mark_warnings)
        if mark_run_scope:
            props["_run_scope"] = mark_run_scope

    font_props, run_scope, font_warnings = font_format_to_officecli_props(
        _paragraph_level_base_effects(text_format.get("base_font")),
        text_format.get("advanced_font"),
        source_base_font=_paragraph_level_base_effects(source.get("base_font")),
        source_advanced_font=source.get("advanced_font"),
        source_office_format=source_office_format,
        scope="paragraph",
        allow_typography=False,
    )
    props.update(font_props)
    warnings.extend(font_warnings)
    if run_scope:
        merged_run_scope = dict(props.get("_run_scope") or {})
        merged_run_scope.update(run_scope)
        props["_run_scope"] = merged_run_scope

    if include_style_name:
        style_name = text_format.get("style_name")
        if style_name is not None:
            props["styleName"] = style_name

    alignment = text_format.get("alignment") or {}
    if alignment.get("alignment") is not None:
        props["align"] = alignment["alignment"]

    outline = text_format.get("outline_level") or {}
    if outline.get("outline_level") is not None:
        props["outlineLvl"] = outline["outline_level"]

    pagination = text_format.get("pagination_control") or {}
    source_pagination = source.get("pagination_control") or {}
    _PAGINATION_MAP = {
        "widow_control": "widowControl",
        "keep_with_next": "keepNext",
        "keep_together": "keepLines",
        "page_break_before": "pageBreakBefore",
        "word_wrap": "wordWrap",
        "contextual_spacing": "contextualSpacing",
    }
    for src_key, office_key in _PAGINATION_MAP.items():
        val = pagination.get(src_key)
        if val is None:
            continue
        if isinstance(val, bool):
            resolved = resolve_bool_write(
                target=val,
                source=source_pagination.get(src_key),
            )
            if resolved is not None:
                props[office_key] = resolved
        else:
            props[office_key] = val

    _apply_spacing_to_props(
        props,
        text_format.get("spacing") or {},
        source_spacing=source.get("spacing"),
        source_office_format=source_office_format,
    )

    _apply_indent_to_props(
        props,
        text_format.get("indent") or {},
        source_office_format=source_office_format,
    )

    list_info = text_format.get("list") or {}
    if list_info.get("num_id") is not None:
        props["numId"] = list_info["num_id"]
        if list_info.get("num_level") is not None:
            props["numLevel"] = list_info["num_level"]
        if list_info.get("start") is not None:
            props["start"] = list_info["start"]
    elif list_info.get("list_style") is not None:
        props["listStyle"] = list_info["list_style"]
        if list_info.get("num_level") is not None:
            props["numLevel"] = list_info["num_level"]
        if list_info.get("start") is not None:
            props["start"] = list_info["start"]

    return props, warnings


class WordParagraphWriter:
    """
    Apply :class:`~LongDocFormatter.officecli.read.text_reader.TextFormatInfo.text_format`
    to body paragraphs via ``officecli set``.

    - Paragraph-level groups → ``set <paragraph_path> --prop ...``
    - ``runs[]`` (``text`` + ``base_font`` / ``advanced_font`` deltas) →
      ``set <paragraph_path> --find TEXT --prop ...`` per entry

    Use 1-based ``paragraph_index`` (same as :class:`WordTextReader.read_by_index`).
    """

    def __init__(
        self,
        doc_path: str | Path,
        officecli: str = "officecli",
        *,
        numbering_source: str | Path | None = None,
    ) -> None:
        self.doc_path = Path(doc_path).resolve()
        self.officecli = officecli
        self.numbering_source = (
            Path(numbering_source).resolve() if numbering_source else None
        )
        self._numbering_mapper: NumberingMapper | None = None
        if not self.doc_path.is_file():
            raise FileNotFoundError(f"Document not found: {self.doc_path}")

    def _numbering_mapper_for(self, text_format: dict[str, Any]) -> NumberingMapper | None:
        list_info = (text_format or {}).get("list") or {}
        needs_mapper = bool(
            self.numbering_source
            or list_info.get("num_fmt")
            or (
                list_info.get("num_id") is not None
                and list_info.get("list_style") not in ("none", "remove", "clear")
            )
        )
        if not needs_mapper:
            return None
        if self._numbering_mapper is None:
            self._numbering_mapper = NumberingMapper(
                self.doc_path,
                source_doc=self.numbering_source,
                officecli=self.officecli,
            )
        return self._numbering_mapper

    def _office_format_at(self, path: str, depth: int = 1) -> dict[str, Any]:
        node = get_element(
            self.doc_path,
            path,
            officecli=self.officecli,
            depth=depth,
        )
        return dict((node or {}).get("format") or {})

    def _run_office_formats_by_text(self, path: str) -> dict[str, dict[str, Any]]:
        node = get_element(
            self.doc_path,
            path,
            officecli=self.officecli,
            depth=2,
        )
        out: dict[str, dict[str, Any]] = {}
        for child in (node or {}).get("children") or []:
            if child.get("type") != "run":
                continue
            text = (child.get("text") or "").strip()
            if not text:
                continue
            out[text] = dict(child.get("format") or {})
        return out

    def _paragraph_text(self, path: str, paragraph_text: str | None = None) -> str:
        if paragraph_text is not None:
            return paragraph_text.strip()
        node = get_element(
            self.doc_path,
            path,
            officecli=self.officecli,
            depth=1,
        )
        return (
            (node or {}).get("text") or (node or {}).get("preview") or ""
        ).strip()

    def _run_paths_in_paragraph(self, path: str) -> list[str]:
        node = get_element(
            self.doc_path,
            path,
            officecli=self.officecli,
            depth=2,
        )
        if not node:
            return []
        return [
            child["path"]
            for child in (node.get("children") or [])
            if child.get("type") == "run" and child.get("path")
        ]

    def _apply_run_scope_props(
        self,
        path: str,
        run_scope: dict[str, Any],
        *,
        paragraph_text: str | None = None,
    ) -> tuple[list[str], list[str]]:
        """
        Apply run-only props to every run under ``path``.

        Falls back to ``--find`` on the full paragraph text when no runs exist.
        """
        warnings: list[str] = []
        properties_set: list[str] = []
        if not run_scope:
            return properties_set, warnings

        run_paths = self._run_paths_in_paragraph(path)
        if run_paths:
            for run_path in run_paths:
                source_fmt = self._office_format_at(run_path, depth=1)
                props = augment_font_props_for_explicit_font(run_scope, source_fmt)
                payload = set_properties(
                    self.doc_path,
                    run_path,
                    props,
                    officecli=self.officecli,
                )
                warnings.extend(extract_officecli_warnings(payload))
            properties_set.extend(sorted(run_scope.keys()))
            return properties_set, warnings

        text = self._paragraph_text(path, paragraph_text)
        if not text:
            warnings.append(
                "Run-scoped font effects skipped: paragraph has no runs and no text."
            )
            return properties_set, warnings

        source_fmt = self._office_format_at(path, depth=2)
        props = augment_font_props_for_explicit_font(run_scope, source_fmt)
        payload = set_with_find(
            self.doc_path,
            path,
            text,
            props,
            officecli=self.officecli,
        )
        warnings.extend(extract_officecli_warnings(payload))
        count = matched_count(payload)
        if count is not None and count < 1:
            warnings.append(f"Run-scoped effects matched 0 times for {path!r}")
        else:
            properties_set.extend(sorted(run_scope.keys()))
        return properties_set, warnings

    def resolve_paragraph_path(
        self,
        paragraph_index: int,
        *,
        body_only: bool = True,
    ) -> str:
        """Return officecli path for a 1-based body paragraph index."""
        reader = WordTextReader(self.doc_path, officecli=self.officecli)
        info = reader.read_by_index(
            paragraph_index,
            body_only=body_only,
            merge_runs=False,
        )
        if info is None:
            raise ParagraphNotFoundError(
                f"No paragraph at index {paragraph_index} in {self.doc_path.name}"
            )
        return info.path

    def apply_text_format(
        self,
        paragraph_index: int,
        text_format: dict[str, Any],
        *,
        body_only: bool = True,
        apply_runs: bool = True,
        apply_style_name: bool = False,
    ) -> TextFormatWriteResult:
        """
        Apply full ``text_format``: paragraph properties, then ``runs`` (if any).

        When ``apply_runs`` is True and ``runs`` is non-empty, each run uses ``--find``.
        """
        try:
            path = self.resolve_paragraph_path(
                paragraph_index, body_only=body_only
            )
        except ParagraphNotFoundError as exc:
            failed = ParagraphWriteResult(
                success=False,
                paragraph_index=paragraph_index,
                path="",
                error=str(exc),
            )
            return TextFormatWriteResult(paragraph=failed, runs=None)

        return self.apply_text_format_at_path(
            path,
            text_format,
            apply_runs=apply_runs,
            apply_style_name=apply_style_name,
        )

    def apply_format(
        self,
        paragraph_index: int,
        text_format: dict[str, Any],
        *,
        body_only: bool = True,
        apply_style_name: bool = False,
    ) -> ParagraphWriteResult:
        """
        Apply paragraph-level ``text_format`` only (``runs`` ignored).

        See :meth:`apply_runs` or :meth:`apply_text_format` for character ranges.
        """
        try:
            path = self.resolve_paragraph_path(
                paragraph_index, body_only=body_only
            )
        except ParagraphNotFoundError as exc:
            return ParagraphWriteResult(
                success=False,
                paragraph_index=paragraph_index,
                path="",
                error=str(exc),
            )
        clean_format = text_format_for_migration(
            {k: v for k, v in text_format.items() if k != "runs"}
        )
        return self.apply_format_at_path(
            path,
            clean_format,
            paragraph_index=paragraph_index,
            apply_style_name=apply_style_name,
        )

    def apply_format_at_path(
        self,
        path: str,
        text_format: dict[str, Any],
        *,
        paragraph_index: int = 0,
        paragraph_text: str | None = None,
        skip_source_text_read: bool = False,
        apply_style_name: bool = False,
    ) -> ParagraphWriteResult:
        """Apply paragraph-level ``text_format`` (no ``runs``) at ``path``."""
        clean_format = text_format_for_migration(
            {k: v for k, v in text_format.items() if k != "runs"}
        )
        warnings: list[str] = []
        mapper = self._numbering_mapper_for(clean_format)
        if mapper is not None:
            clean_format, map_warnings = mapper.resolve_text_format(clean_format)
            warnings.extend(map_warnings)

        source_text_format: dict[str, Any] | None = None
        if not skip_source_text_read:
            try:
                source_info = WordTextReader(self.doc_path, officecli=self.officecli).read_at(
                    path,
                    merge_runs=True,
                )
                if source_info is not None:
                    source_text_format = dict(source_info.text_format or {})
            except Exception:
                source_text_format = None
        source_office_format = self._office_format_at(path, depth=2)

        props, prop_warnings = text_format_to_officecli_props(
            clean_format,
            source_text_format=source_text_format,
            source_office_format=source_office_format,
            include_style_name=apply_style_name,
        )
        warnings.extend(prop_warnings)
        run_scope = props.pop("_run_scope", None) or {}
        properties_set: list[str] = []

        if not props and not run_scope:
            return ParagraphWriteResult(
                success=False,
                paragraph_index=paragraph_index,
                path=path,
                warnings=warnings,
                error="No supported properties in text_format.",
            )

        try:
            if props:
                payload = set_properties(
                    self.doc_path,
                    path,
                    props,
                    officecli=self.officecli,
                )
                warnings.extend(extract_officecli_warnings(payload))
                properties_set.extend(sorted(props.keys()))

            if run_scope:
                run_props, run_warnings = self._apply_run_scope_props(
                    path,
                    run_scope,
                    paragraph_text=paragraph_text,
                )
                warnings.extend(run_warnings)
                properties_set.extend(run_props)

            return ParagraphWriteResult(
                success=True,
                paragraph_index=paragraph_index,
                path=path,
                properties_set=sorted(set(properties_set)),
                warnings=warnings,
            )
        except OfficeCliError as exc:
            return ParagraphWriteResult(
                success=False,
                paragraph_index=paragraph_index,
                path=path,
                properties_set=sorted(set(properties_set)),
                warnings=warnings,
                error=str(exc),
            )

    def apply_font_to_all_runs_at_path(
        self,
        path: str,
        text_format: dict[str, Any],
        *,
        paragraph_text: str | None = None,
        precomputed_run_props: dict[str, Any] | None = None,
    ) -> list[str]:
        """
        Force ``base_font`` / ``advanced_font`` onto every run under ``path``.

        Clears theme font bindings (``font.*Theme``) that would otherwise make Word
        display theme fonts (e.g. 等线) despite explicit ``font.ea``.
        """
        warnings: list[str] = []
        if precomputed_run_props is not None:
            run_props = dict(precomputed_run_props)
        else:
            run_props, prop_warnings = migration_uniform_run_font_props(
                text_format.get("base_font"),
            )
            warnings.extend(prop_warnings)
        if not run_props:
            return warnings

        run_paths = self._run_paths_in_paragraph(path)
        if run_paths:
            for run_path in run_paths:
                source_fmt = self._office_format_at(run_path, depth=1)
                props = augment_font_props_for_explicit_font(run_props, source_fmt)
                try:
                    set_properties(
                        self.doc_path,
                        run_path,
                        props,
                        officecli=self.officecli,
                    )
                except OfficeCliError as exc:
                    warnings.append(f"{run_path}: run font failed: {exc}")
            return warnings

        text = self._paragraph_text(path, paragraph_text)
        if not text:
            warnings.append(f"{path}: no runs/text for font apply")
            return warnings
        source_fmt = self._office_format_at(path, depth=2)
        props = augment_font_props_for_explicit_font(run_props, source_fmt)
        try:
            payload = set_with_find(
                self.doc_path,
                path,
                text,
                props,
                officecli=self.officecli,
            )
            warnings.extend(extract_officecli_warnings(payload))
            count = matched_count(payload)
            if count is not None and count < 1:
                warnings.append(f"{path}: font find matched 0")
        except OfficeCliError as exc:
            warnings.append(f"{path}: font find failed: {exc}")
        return warnings

    def apply_text_format_at_path(
        self,
        path: str,
        text_format: dict[str, Any],
        *,
        paragraph_text: str | None = None,
        apply_runs: bool = True,
        skip_source_text_read: bool = False,
        precomputed_run_props: dict[str, Any] | None = None,
        apply_style_name: bool = False,
    ) -> TextFormatWriteResult:
        """Apply full ``text_format`` at ``path`` (paragraph props + optional ``runs``)."""
        text_format = text_format_for_migration(text_format)
        runs = text_format.get("runs") if apply_runs else None
        paragraph_format = {k: v for k, v in text_format.items() if k != "runs"}
        para_result = self.apply_format_at_path(
            path,
            paragraph_format,
            paragraph_text=paragraph_text,
            skip_source_text_read=skip_source_text_read,
            apply_style_name=apply_style_name,
        )
        runs_result = None
        if runs:
            runs_result = self.apply_runs_at_path(path, runs)
        if needs_uniform_run_font_migration(text_format.get("base_font")):
            para_result.warnings.extend(
                self.apply_font_to_all_runs_at_path(
                    path,
                    text_format,
                    paragraph_text=paragraph_text,
                    precomputed_run_props=precomputed_run_props,
                )
            )
        return TextFormatWriteResult(paragraph=para_result, runs=runs_result)

    def apply_text_format_to_paths(
        self,
        paths: list[str],
        text_format: dict[str, Any],
        *,
        paragraph_texts: dict[str, str] | None = None,
        apply_runs: bool = True,
        skip_source_text_read: bool = True,
        apply_style_name: bool = False,
    ) -> list[str]:
        """
        Apply the same ``text_format`` to multiple paragraph paths (batch by style tag).

        Skips per-paragraph ``WordTextReader.read_at`` when ``skip_source_text_read`` is
        True (template is the format source). Precomputes run font props once per batch.
        Does not write ``style_name`` unless ``apply_style_name`` is True.
        """
        warnings: list[str] = []
        if not paths:
            return warnings

        text_format = text_format_for_migration(text_format)
        texts = paragraph_texts or {}
        precomputed_run_props: dict[str, Any] | None = None
        if needs_uniform_run_font_migration(text_format.get("base_font")):
            precomputed_run_props, prop_warnings = migration_uniform_run_font_props(
                text_format.get("base_font"),
            )
            warnings.extend(prop_warnings)

        payload = dict(text_format)
        if not apply_runs:
            payload.pop("runs", None)

        for path in paths:
            result = self.apply_text_format_at_path(
                path,
                payload,
                paragraph_text=texts.get(path),
                apply_runs=apply_runs,
                skip_source_text_read=skip_source_text_read,
                precomputed_run_props=precomputed_run_props,
                apply_style_name=apply_style_name,
            )
            if not result.success:
                err = result.paragraph.error or (
                    result.runs.error if result.runs else "write failed"
                )
                warnings.append(f"{path}: {err}")
            warnings.extend(result.paragraph.warnings)
            if result.runs:
                warnings.extend(result.runs.warnings)
        return warnings

    def apply_runs(
        self,
        paragraph_index: int,
        runs: list[dict[str, Any]],
        *,
        body_only: bool = True,
    ) -> ParagraphRunsWriteResult:
        """Apply ``runs[]`` using ``--find`` for each entry's ``text``."""
        try:
            path = self.resolve_paragraph_path(
                paragraph_index, body_only=body_only
            )
        except ParagraphNotFoundError as exc:
            return ParagraphRunsWriteResult(
                success=False,
                paragraph_index=paragraph_index,
                path="",
                error=str(exc),
            )
        return self.apply_runs_at_path(path, runs, paragraph_index=paragraph_index)

    def apply_run_targets_at_path(
        self,
        path: str,
        run_targets: list[dict[str, Any]],
        *,
        paragraph_text: str | None = None,
        paragraph_index: int = 0,
    ) -> ParagraphRunsWriteResult:
        """Apply anchored run targets via context-aware ``--find``."""
        if not run_targets:
            return ParagraphRunsWriteResult(
                success=True,
                paragraph_index=paragraph_index,
                path=path,
            )

        text = paragraph_text
        if text is None:
            try:
                info = WordTextReader(self.doc_path, officecli=self.officecli).read_at(
                    path,
                    merge_runs=True,
                )
                text = (info.text if info else "") or ""
            except Exception:
                text = ""

        all_warnings: list[str] = []
        results: list[RunApplyResult] = []
        overall_ok = True

        for target in run_targets:
            if not isinstance(target, dict):
                continue
            match = resolve_run_target_match(text or "", target)
            find_text = (target.get("text") or "").strip()
            if not match.matched:
                all_warnings.append(
                    f"{path}: {match.warning or f'could not resolve target {find_text!r}'}"
                )
                results.append(
                    RunApplyResult(
                        text=find_text,
                        matched=0,
                        success=False,
                        error=match.warning or "no match",
                    )
                )
                overall_ok = False
                continue

            props, warnings = run_entry_to_officecli_props(target, source_run={})
            all_warnings.extend(warnings)
            if not props:
                overall_ok = False
                results.append(
                    RunApplyResult(
                        text=find_text,
                        matched=0,
                        success=False,
                        error="No font properties in run target.",
                    )
                )
                continue
            try:
                payload = set_with_find(
                    self.doc_path,
                    path,
                    match.text,
                    props,
                    officecli=self.officecli,
                )
                all_warnings.extend(extract_officecli_warnings(payload))
                count = matched_count(payload)
                matched = count if count is not None else 0
                ok = matched > 0
                if not ok:
                    all_warnings.append(f"find={match.text!r} matched 0 times in {path}")
                    overall_ok = False
                results.append(
                    RunApplyResult(
                        text=match.text,
                        matched=matched,
                        properties_set=sorted(props.keys()),
                        success=ok,
                        error="" if ok else "no match",
                    )
                )
            except OfficeCliError as exc:
                overall_ok = False
                results.append(
                    RunApplyResult(
                        text=find_text,
                        matched=0,
                        properties_set=sorted(props.keys()),
                        success=False,
                        error=str(exc),
                    )
                )

        return ParagraphRunsWriteResult(
            success=overall_ok,
            paragraph_index=paragraph_index,
            path=path,
            run_results=results,
            warnings=all_warnings,
        )

    def apply_runs_at_path(
        self,
        path: str,
        runs: list[dict[str, Any]],
        *,
        paragraph_index: int = 0,
    ) -> ParagraphRunsWriteResult:
        """
        Apply ``runs[]`` entries via ``officecli set --find``.

        Each run dict:

        - ``text`` (required): substring to match in the paragraph
        - ``base_font`` / ``advanced_font`` (optional): font deltas, same keys as reader
        - ``path`` (optional): ignored on write (reader metadata only)
        """
        if not runs:
            return ParagraphRunsWriteResult(
                success=True,
                paragraph_index=paragraph_index,
                path=path,
            )

        all_warnings: list[str] = []
        results: list[RunApplyResult] = []
        overall_ok = True

        source_runs_by_text: dict[str, dict[str, Any]] = {}
        run_office_by_text = self._run_office_formats_by_text(path)
        try:
            source_info = WordTextReader(self.doc_path, officecli=self.officecli).read_at(
                path,
                merge_runs=True,
            )
            if source_info is not None:
                for entry in (source_info.text_format or {}).get("runs") or []:
                    text_key = (entry.get("text") or "").strip()
                    if text_key:
                        source_runs_by_text[text_key] = entry
        except Exception:
            source_runs_by_text = {}

        for run in runs:
            text = (run.get("text") or "").strip()
            if not text:
                all_warnings.append("Skipped run entry with empty text.")
                results.append(
                    RunApplyResult(text="", matched=0, success=False, error="empty text")
                )
                overall_ok = False
                continue

            source_run = dict(source_runs_by_text.get(text) or {})
            source_run["office_format"] = run_office_by_text.get(text) or {}
            props, warnings = run_entry_to_officecli_props(
                run,
                source_run=source_run,
            )
            all_warnings.extend(warnings)
            if not props:
                results.append(
                    RunApplyResult(
                        text=text,
                        matched=0,
                        success=False,
                        error="No font properties in run entry.",
                    )
                )
                overall_ok = False
                continue

            try:
                payload = set_with_find(
                    self.doc_path,
                    path,
                    text,
                    props,
                    officecli=self.officecli,
                )
                all_warnings.extend(extract_officecli_warnings(payload))
                count = matched_count(payload)
                matched = count if count is not None else 0
                ok = matched > 0
                if not ok:
                    all_warnings.append(f"find={text!r} matched 0 times in {path}")
                    overall_ok = False
                results.append(
                    RunApplyResult(
                        text=text,
                        matched=matched,
                        properties_set=sorted(props.keys()),
                        success=ok,
                        error="" if ok else "no match",
                    )
                )
            except OfficeCliError as exc:
                overall_ok = False
                results.append(
                    RunApplyResult(
                        text=text,
                        matched=0,
                        properties_set=sorted(props.keys()),
                        success=False,
                        error=str(exc),
                    )
                )

        return ParagraphRunsWriteResult(
            success=overall_ok,
            paragraph_index=paragraph_index,
            path=path,
            run_results=results,
            warnings=all_warnings,
        )
