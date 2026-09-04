"""Write page/section format via officecli (schema matches page_reader)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from LongDocFormatter.officecli.read.page_reader import PageFormatInfo, WordPageReader
from LongDocFormatter.officecli.read.paper_format import normalize_page_format
from LongDocFormatter.officecli.read.page_schema import (
    border_line_to_pbdr_bottom,
    page_number_from_footer_slot,
    resolve_header_footer_refs,
)

from ._cli import (
    OfficeCliError,
    add_element,
    clear_header_footer_content,
    extract_officecli_warnings,
    get_element,
    query_elements,
    set_properties,
)
from ._format_props import augment_font_props_for_explicit_font
from .page_schema import section_props_from_page_format, skipped_page_num_fmt_warnings


class SectionMappingError(Exception):
    """Section index mapping between source and target is invalid."""

    def __init__(self, message: str, *, details: list[str] | None = None) -> None:
        super().__init__(message)
        self.details = details or []


@dataclass
class SectionMappingEntry:
    """One source section -> target section mapping record."""

    source_index: int
    target_index: int
    source_path: str
    target_path: str
    status: str = "pending"
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_index": self.source_index,
            "target_index": self.target_index,
            "source_path": self.source_path,
            "target_path": self.target_path,
            "status": self.status,
            "message": self.message,
        }


@dataclass
class PageWriteResult:
    """Result of a page format write operation."""

    success: bool
    mapping_table: list[SectionMappingEntry] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "mapping_table": [m.to_dict() for m in self.mapping_table],
            "errors": self.errors,
            "warnings": self.warnings,
        }


def build_one_to_one_mapping(
    source_sections: list[PageFormatInfo],
    target_sections: list[PageFormatInfo],
    *,
    strict: bool = True,
) -> tuple[dict[int, int], list[SectionMappingEntry]]:
    """
    Build ``{source_index: target_index}`` 1:1 mapping by section index.

    Raises :class:`SectionMappingError` when counts differ and ``strict`` is True.
    """
    source_indices = sorted(s.section_index for s in source_sections)
    target_indices = sorted(s.section_index for s in target_sections)
    entries: list[SectionMappingEntry] = []

    if len(source_indices) != len(target_indices):
        msg = (
            f"Section count mismatch: source has {len(source_indices)} "
            f"(indices {source_indices}), target has {len(target_indices)} "
            f"(indices {target_indices})."
        )
        if strict:
            raise SectionMappingError(
                msg,
                details=[
                    "Provide an explicit mapping dict to override, "
                    "or align section counts in both documents."
                ],
            )
    mapping: dict[int, int] = {}
    pair_count = min(len(source_indices), len(target_indices))
    for i in range(pair_count):
        src_idx = source_indices[i]
        tgt_idx = target_indices[i]
        src_path = next(s.path for s in source_sections if s.section_index == src_idx)
        tgt_path = next(s.path for s in target_sections if s.section_index == tgt_idx)
        mapping[src_idx] = tgt_idx
        entries.append(
            SectionMappingEntry(
                source_index=src_idx,
                target_index=tgt_idx,
                source_path=src_path,
                target_path=tgt_path,
                status="mapped",
            )
        )
    if len(source_indices) > pair_count:
        for src_idx in source_indices[pair_count:]:
            entries.append(
                SectionMappingEntry(
                    source_index=src_idx,
                    target_index=-1,
                    source_path=next(s.path for s in source_sections if s.section_index == src_idx),
                    target_path="",
                    status="skipped",
                    message="No matching target section (source has extra sections).",
                )
            )
    if len(target_indices) > pair_count:
        for tgt_idx in target_indices[pair_count:]:
            entries.append(
                SectionMappingEntry(
                    source_index=-1,
                    target_index=tgt_idx,
                    source_path="",
                    target_path=next(s.path for s in target_sections if s.section_index == tgt_idx),
                    status="skipped",
                    message="No matching source section (target has extra sections).",
                )
            )
    return mapping, entries


SLOT_OFFICECLI_TYPES = {
    "primary": "default",
    "first": "first",
    "even": "even",
}


def _flag_from_header_footer(page_format: dict[str, Any], key: str) -> bool | None:
    header = page_format.get("header") or {}
    footer = page_format.get("footer") or {}
    if key in header:
        return bool(header[key])
    if key in footer:
        return bool(footer[key])
    return None


def _aggregate_document_props(
    source_sections: list[PageFormatInfo],
    target_sections: list[PageFormatInfo] | None = None,
) -> dict[str, Any]:
    """Merge document-level settings (even/odd headers)."""
    from ._format_props import resolve_bool_write

    even_and_odd: bool | None = None
    for section in source_sections:
        page_format = section.page_format
        odd_even = _flag_from_header_footer(page_format, "different_odd_even")
        if odd_even is True:
            even_and_odd = True
        elif odd_even is False and even_and_odd is None:
            even_and_odd = False

    props: dict[str, Any] = {}
    if even_and_odd is not None:
        source_current: bool | None = None
        if target_sections:
            source_current = _flag_from_header_footer(
                target_sections[0].page_format,
                "different_odd_even",
            )
        resolved = resolve_bool_write(target=even_and_odd, source=source_current)
        if resolved is not None:
            props["evenAndOddHeaders"] = resolved
    return props


def _section_props_from_page_format(
    page_format: dict[str, Any],
    *,
    source_page_format: dict[str, Any] | None = None,
    source_section_fmt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return section_props_from_page_format(
        page_format,
        source_page_format=source_page_format,
        source_section_fmt=source_section_fmt,
    )


def clear_all_header_footer_slots(
    doc_path: Path,
    *,
    officecli: str = "officecli",
) -> list[str]:
    """
    Clear every header/footer slot (and first paragraph) before applying page format.

    Prevents duplicate PAGE fields when template footer is re-written onto content
    that already contains page-number fields.
    """
    warnings: list[str] = []
    for selector in ("header", "footer"):
        for node in query_elements(doc_path, selector, officecli=officecli):
            slot_path = str(node.get("path") or "").strip()
            if not slot_path:
                continue
            for path in (slot_path, f"{slot_path}/p[1]"):
                try:
                    clear_header_footer_content(
                        doc_path,
                        path,
                        officecli=officecli,
                    )
                except OfficeCliError as exc:
                    warnings.append(f"clear {path}: {exc}")
    return warnings


def _slot_is_present(slot: dict[str, Any] | None) -> bool:
    if not slot:
        return False
    if "present" in slot:
        return slot.get("present") is True
    if page_number_from_footer_slot(slot):
        return True
    return bool((slot.get("text") or "").strip())


def _footer_static_text(slot: dict[str, Any]) -> str | None:
    """Literal footer text excluding page-number placeholders."""
    text = (slot.get("text") or "").strip()
    if not text:
        return None
    if page_number_from_footer_slot(slot) and text.isdigit():
        return None
    return text


def _footer_has_page_number(slot: dict[str, Any]) -> bool:
    if not _slot_is_present(slot):
        return False
    page_number = page_number_from_footer_slot(slot)
    if page_number:
        return True
    return not bool((slot.get("text") or "").strip())


def _normalize_odd_even_footer_slots(page_format: dict[str, Any]) -> dict[str, Any]:
    """
    When odd/even footers differ, ensure the even slot inherits page-number settings
    from primary if the reader missed it (stale cache / SDT placeholder).
    """
    pf = dict(page_format)
    footer = dict(pf.get("footer") or {})
    if _flag_from_header_footer(pf, "different_odd_even") is not True:
        return pf

    primary = footer.get("primary") or {}
    if not _footer_has_page_number(primary):
        return pf

    even = dict(footer.get("even") or {})
    if _footer_has_page_number(even):
        return pf

    page_number = dict(page_number_from_footer_slot(primary))
    page_number["alignment"] = (
        (even.get("page_number") or {}).get("alignment")
        or even.get("align")
        or "right"
    )
    even["present"] = True
    even["page_number"] = page_number
    footer["even"] = even
    pf["footer"] = footer
    return pf


def _header_slot_props(slot: dict[str, Any]) -> dict[str, Any]:
    props: dict[str, Any] = {}
    if slot.get("text") is not None:
        props["text"] = slot.get("text")
    if slot.get("align") is not None:
        props["align"] = slot.get("align")
    name = slot.get("name") or slot.get("font")
    if name is not None:
        props["font"] = name
    if slot.get("name_ascii") is not None:
        props["font.latin"] = slot.get("name_ascii")
    if slot.get("name_far_east") is not None:
        props["font.ea"] = slot.get("name_far_east")
    if slot.get("size") is not None:
        props["size"] = slot.get("size")
    return props


def _run_paths_under(
    doc_path: Path,
    path: str,
    *,
    officecli: str,
) -> list[str]:
    node = get_element(doc_path, path, officecli=officecli, depth=2)
    if not node:
        return []
    return [
        child["path"]
        for child in (node.get("children") or [])
        if child.get("type") == "run" and child.get("path")
    ]


def _font_props_from_slot_props(props: dict[str, Any]) -> dict[str, Any]:
    return _omit_none_dict(
        {
            key: props[key]
            for key in ("font", "font.latin", "font.ea", "size")
            if props.get(key) is not None
        }
    )


def _augment_slot_font_props(
    props: dict[str, Any],
    source_office_format: dict[str, Any] | None = None,
) -> dict[str, Any]:
    font_props = _font_props_from_slot_props(props)
    return augment_font_props_for_explicit_font(font_props, source_office_format)


def _omit_none_dict(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if v is not None}


def _apply_font_to_runs_under(
    doc_path: Path,
    path: str,
    font_props: dict[str, Any],
    *,
    officecli: str,
) -> list[str]:
    warnings: list[str] = []
    if not font_props:
        return warnings
    for run_path in _run_paths_under(doc_path, path, officecli=officecli):
        try:
            run_node = get_element(doc_path, run_path, officecli=officecli, depth=1)
            source_fmt = dict((run_node or {}).get("format") or {})
            props = augment_font_props_for_explicit_font(font_props, source_fmt)
            payload = set_properties(
                doc_path,
                run_path,
                props,
                officecli=officecli,
            )
            warnings.extend(extract_officecli_warnings(payload))
        except OfficeCliError as exc:
            warnings.append(f"Failed to set run font at {run_path}: {exc}")
    return warnings


def _footer_slot_props(
    slot: dict[str, Any],
    *,
    static_text: str | None = None,
) -> dict[str, Any]:
    props: dict[str, Any] = {}
    if static_text:
        props["text"] = static_text
    page_number = page_number_from_footer_slot(slot)
    if page_number.get("alignment") is not None:
        props["align"] = page_number.get("alignment")
    elif slot.get("align") is not None:
        props["align"] = slot.get("align")
    name = page_number.get("name") or slot.get("name") or slot.get("font")
    if name is not None:
        props["font"] = name
    if page_number.get("name_ascii") is not None:
        props["font.latin"] = page_number.get("name_ascii")
    elif slot.get("name_ascii") is not None:
        props["font.latin"] = slot.get("name_ascii")
    if page_number.get("name_far_east") is not None:
        props["font.ea"] = page_number.get("name_far_east")
    elif slot.get("name_far_east") is not None:
        props["font.ea"] = slot.get("name_far_east")
    size = page_number.get("size")
    if size is None:
        size = slot.get("size")
    if size is not None:
        props["size"] = size
    return props


def _header_border_props(slot: dict[str, Any]) -> dict[str, Any]:
    if "border_line" not in slot:
        return {}
    pbdr = border_line_to_pbdr_bottom(slot.get("border_line"))
    if pbdr is None:
        return {}
    return {"pbdr.bottom": pbdr}


def _footer_add_props(source_slot: dict[str, Any], *, officecli_type: str) -> dict[str, Any]:
    props: dict[str, Any] = {"type": officecli_type}
    static_text = _footer_static_text(source_slot)
    content = _footer_slot_props(source_slot, static_text=static_text)
    if _footer_has_page_number(source_slot) and not static_text:
        content.setdefault("field", "page")
    props.update(content)
    if "text" not in props and static_text:
        props["text"] = static_text
    if "text" not in props and not props.get("field") and not _footer_has_page_number(source_slot):
        props["text"] = source_slot.get("text") or ""
    return props


def _header_add_props(source_slot: dict[str, Any], *, officecli_type: str) -> dict[str, Any]:
    props: dict[str, Any] = {"type": officecli_type}
    props.update(_header_slot_props(source_slot))
    if "text" not in props:
        props["text"] = source_slot.get("text") or ""
    return props


def _slot_needs_ref(
    source_part: dict[str, Any],
    slot_name: str,
    *,
    feature_enabled: bool,
) -> bool:
    if slot_name == "primary":
        slot = source_part.get("primary") or {}
        if slot.get("present") is False:
            return False
        return _slot_is_present(slot) or slot.get("present") is True
    if not feature_enabled:
        return False
    slot = source_part.get(slot_name)
    if slot is None:
        return True
    if slot.get("present") is False:
        return True
    return _slot_is_present(slot) or slot.get("present") is True


def _add_props_for_new_slot(
    source_slot: dict[str, Any],
    *,
    part: str,
    officecli_type: str,
) -> dict[str, Any]:
    """Create an empty header/footer part; content is applied in ``_write_header_footer_part``."""
    return {"type": officecli_type}


class WordPageWriter:
    """
    Write :class:`PageFormatInfo` data to a .docx via officecli.

    Schema matches :class:`~LongDocFormatter.officecli.read.page_reader.WordPageReader`.
    """

    SLOT_NAMES = ("primary", "first", "even")

    def __init__(self, doc_path: str | Path, officecli: str = "officecli") -> None:
        self.doc_path = Path(doc_path).resolve()
        self.officecli = officecli
        if not self.doc_path.is_file():
            raise FileNotFoundError(f"Document not found: {self.doc_path}")

    def _target_sections(self) -> list[PageFormatInfo]:
        return WordPageReader(self.doc_path, officecli=self.officecli).read_sections()

    def write_sections(
        self,
        source_sections: list[PageFormatInfo],
        mapping: dict[int, int] | None = None,
        *,
        strict_mapping: bool = True,
    ) -> PageWriteResult:
        """
        Write page format from ``source_sections`` into this document.

        Parameters
        ----------
        source_sections:
            Sections read from the source document (e.g. report.docx).
        mapping:
            ``{source_section_index: target_section_index}``. When ``None``,
            builds a 1:1 mapping by sorted section index.
        strict_mapping:
            If True, raise :class:`SectionMappingError` when section counts differ
            and ``mapping`` is None.
        """
        target_sections = self._target_sections()
        target_by_index = {s.section_index: s for s in target_sections}
        source_by_index = {s.section_index: s for s in source_sections}

        if mapping is None:
            mapping, table = build_one_to_one_mapping(
                source_sections,
                target_sections,
                strict=strict_mapping,
            )
        else:
            table = []
            for src_idx, tgt_idx in sorted(mapping.items()):
                src = source_by_index.get(src_idx)
                tgt = target_by_index.get(tgt_idx)
                if src is None:
                    raise SectionMappingError(
                        f"Source section index {src_idx} not found in source_sections."
                    )
                if tgt is None:
                    raise SectionMappingError(
                        f"Target section index {tgt_idx} not found in {self.doc_path}."
                    )
                table.append(
                    SectionMappingEntry(
                        source_index=src_idx,
                        target_index=tgt_idx,
                        source_path=src.path,
                        target_path=tgt.path,
                        status="mapped",
                    )
                )

        errors: list[str] = []
        warnings: list[str] = []

        doc_props = _aggregate_document_props(source_sections, target_sections)
        if doc_props:
            try:
                payload = set_properties(
                    self.doc_path,
                    "/",
                    doc_props,
                    officecli=self.officecli,
                )
                warnings.extend(extract_officecli_warnings(payload))
            except OfficeCliError as exc:
                errors.append(f"Document properties: {exc}")

        for entry in table:
            if entry.status != "mapped":
                continue
            src = source_by_index[entry.source_index]
            tgt = target_by_index[entry.target_index]
            try:
                section_warnings = self._write_one_section(
                    src.page_format,
                    target_section=tgt,
                )
                entry.status = "ok"
                warnings.extend(section_warnings)
            except OfficeCliError as exc:
                entry.status = "error"
                entry.message = str(exc)
                errors.append(
                    f"Section {entry.source_index} -> {entry.target_index}: {exc}"
                )
            except Exception as exc:
                entry.status = "error"
                entry.message = str(exc)
                errors.append(
                    f"Section {entry.source_index} -> {entry.target_index}: {exc}"
                )

        success = not errors and all(e.status == "ok" for e in table if e.status == "mapped")
        return PageWriteResult(
            success=success,
            mapping_table=table,
            errors=errors,
            warnings=warnings,
        )

    def _section_header_footer_refs(self, section_path: str) -> tuple[dict[str, Any], dict[str, Any]]:
        node = get_element(
            self.doc_path,
            section_path,
            officecli=self.officecli,
        )
        section_fmt = dict((node or {}).get("format") or {})
        return (
            resolve_header_footer_refs(section_fmt, "header"),
            resolve_header_footer_refs(section_fmt, "footer"),
        )

    def _write_one_section(
        self,
        page_format: dict[str, Any],
        *,
        target_section: PageFormatInfo,
    ) -> list[str]:
        warnings: list[str] = []
        page_format = normalize_page_format(page_format)
        page_format = _normalize_odd_even_footer_slots(page_format)
        section_path = target_section.path
        warnings.extend(skipped_page_num_fmt_warnings(page_format))

        section_node = get_element(
            self.doc_path,
            section_path,
            officecli=self.officecli,
        )
        source_section_fmt = dict((section_node or {}).get("format") or {})

        section_props = _section_props_from_page_format(
            page_format,
            source_page_format=dict(target_section.page_format or {}),
            source_section_fmt=source_section_fmt,
        )
        if section_props:
            payload = set_properties(
                self.doc_path,
                section_path,
                section_props,
                officecli=self.officecli,
            )
            warnings.extend(extract_officecli_warnings(payload))

        warnings.extend(
            self._ensure_header_footer_refs(page_format, target_section=target_section)
        )

        refreshed = WordPageReader(self.doc_path, officecli=self.officecli).read_section_at(
            target_section.section_index
        )
        if refreshed:
            target_section = refreshed

        header_refs, footer_refs = self._section_header_footer_refs(section_path)
        source_header = page_format.get("header") or {}
        warnings.extend(
            self._write_header_footer_part(
                source_header,
                header_refs,
                part="header",
            )
        )

        source_footer = page_format.get("footer") or {}
        warnings.extend(
            self._write_header_footer_part(
                source_footer,
                footer_refs,
                part="footer",
            )
        )
        return warnings

    def _ensure_header_footer_refs(
        self,
        page_format: dict[str, Any],
        *,
        target_section: PageFormatInfo,
    ) -> list[str]:
        warnings: list[str] = []
        section_path = target_section.path
        header_refs, footer_refs = self._section_header_footer_refs(section_path)
        different_first = _flag_from_header_footer(page_format, "different_first_page") is True
        different_odd_even = _flag_from_header_footer(page_format, "different_odd_even") is True

        for part, refs in (("header", header_refs), ("footer", footer_refs)):
            source_part = page_format.get(part) or {}
            feature_by_slot = {
                "primary": True,
                "first": different_first,
                "even": different_odd_even,
            }
            for slot_name, officecli_type in SLOT_OFFICECLI_TYPES.items():
                if not feature_by_slot.get(slot_name, False):
                    continue
                if refs.get(slot_name):
                    continue
                if not _slot_needs_ref(
                    source_part,
                    slot_name,
                    feature_enabled=feature_by_slot[slot_name],
                ):
                    continue
                source_slot = source_part.get(slot_name) or {}
                add_props = _add_props_for_new_slot(
                    source_slot,
                    part=part,
                    officecli_type=officecli_type,
                )
                try:
                    payload = add_element(
                        self.doc_path,
                        section_path,
                        part,
                        add_props,
                        officecli=self.officecli,
                    )
                    warnings.extend(extract_officecli_warnings(payload))
                except OfficeCliError as exc:
                    warnings.append(
                        f"Failed to add {part} slot '{slot_name}' at {section_path}: {exc}"
                    )
        return warnings

    def _write_header_footer_part(
        self,
        source_part: dict[str, Any],
        target_refs: dict[str, Any],
        *,
        part: str,
    ) -> list[str]:
        warnings: list[str] = []
        for slot_name in self.SLOT_NAMES:
            source_slot = source_part.get(slot_name) or {}
            target_path = target_refs.get(slot_name)
            if not target_path:
                if _slot_is_present(source_slot):
                    warnings.append(
                        f"Target has no {part} ref for slot '{slot_name}'; skipped."
                    )
                continue

            if not _slot_is_present(source_slot):
                continue

            try:
                clear_header_footer_content(
                    self.doc_path,
                    target_path,
                    officecli=self.officecli,
                )
                para_path = f"{target_path}/p[1]"
                try:
                    clear_header_footer_content(
                        self.doc_path,
                        para_path,
                        officecli=self.officecli,
                    )
                except OfficeCliError:
                    pass
            except OfficeCliError as exc:
                warnings.append(
                    f"Failed to clear {part} slot '{slot_name}' at {target_path}: {exc}"
                )

            if part == "header":
                props = _header_slot_props(source_slot)
                border_props = _header_border_props(source_slot)
                needs_page_field = False
            else:
                static_text = _footer_static_text(source_slot)
                props = _footer_slot_props(source_slot, static_text=static_text)
                border_props = {}
                needs_page_field = _footer_has_page_number(source_slot) and not static_text
            if not props and not needs_page_field:
                warnings.append(
                    f"{part} slot '{slot_name}' present but no writable props; left cleared."
                )
                continue
            try:
                para_path = f"{target_path}/p[1]"
                if part == "footer" and needs_page_field:
                    payload = add_element(
                        self.doc_path,
                        para_path,
                        "field",
                        {"fieldType": "page"},
                        officecli=self.officecli,
                    )
                    warnings.extend(extract_officecli_warnings(payload))
                    props.pop("text", None)
                    if props:
                        para_fmt = dict(
                            (get_element(
                                self.doc_path,
                                para_path,
                                officecli=self.officecli,
                                depth=1,
                            ) or {}).get("format") or {}
                        )
                        para_props = augment_font_props_for_explicit_font(props, para_fmt)
                        payload = set_properties(
                            self.doc_path,
                            para_path,
                            para_props,
                            officecli=self.officecli,
                        )
                        warnings.extend(extract_officecli_warnings(payload))
                    warnings.extend(
                        _apply_font_to_runs_under(
                            self.doc_path,
                            para_path,
                            _augment_slot_font_props(props),
                            officecli=self.officecli,
                        )
                    )
                else:
                    if props:
                        slot_fmt = dict(
                            (get_element(
                                self.doc_path,
                                target_path,
                                officecli=self.officecli,
                                depth=1,
                            ) or {}).get("format") or {}
                        )
                        write_props = augment_font_props_for_explicit_font(props, slot_fmt)
                        payload = set_properties(
                            self.doc_path,
                            target_path,
                            write_props,
                            officecli=self.officecli,
                        )
                        warnings.extend(extract_officecli_warnings(payload))
                    if part == "footer" and props:
                        warnings.extend(
                            _apply_font_to_runs_under(
                                self.doc_path,
                                para_path,
                                _augment_slot_font_props(props),
                                officecli=self.officecli,
                            )
                        )
                if border_props:
                    payload = set_properties(
                        self.doc_path,
                        para_path,
                        border_props,
                        officecli=self.officecli,
                    )
                    warnings.extend(extract_officecli_warnings(payload))
            except OfficeCliError as exc:
                raise OfficeCliError(
                    f"Failed to set {part} {slot_name} at {target_path}: {exc}"
                ) from exc
        return warnings

    def write_from_reader(
        self,
        source_reader: WordPageReader,
        mapping: dict[int, int] | None = None,
        *,
        strict_mapping: bool = True,
    ) -> PageWriteResult:
        """Read sections from ``source_reader`` and write into this document."""
        return self.write_sections(
            source_reader.read_sections(),
            mapping=mapping,
            strict_mapping=strict_mapping,
        )
