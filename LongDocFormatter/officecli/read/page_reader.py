"""Read Word page/section layout via officecli (flat schema for read/write)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ._cli import get_element, query_elements, run_officecli
from ._ooxml_settings import read_even_and_odd_headers
from .footer_ooxml import alignment_for_section_footer_slot
from .format_schema import _scalar_from_fmt
from .page_schema import (
    build_columns,
    build_grid,
    build_page_number,
    enrich_page_number_defaults,
    parse_border_line,
    resolve_header_footer_refs,
    resolve_section_page_start,
)


def _path_index(path: str, element: str) -> int | None:
    match = re.search(rf"/{element}\[(\d+)\]", path)
    return int(match.group(1)) if match else None


def _omit_none(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if v is not None}


def _first_paragraph(node: dict[str, Any]) -> dict[str, Any] | None:
    for child in node.get("children") or []:
        if child.get("type") == "paragraph":
            return child
    return None


def _pick(fmt: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in fmt and fmt[key] is not None:
            return fmt[key]
    return None


def _alignment(fmt: dict[str, Any]) -> str | None:
    raw = _pick(fmt, "align", "alignment", "effective.alignment")
    return str(raw).lower() if raw is not None else None


def _font_names_triplet(fmt: dict[str, Any]) -> dict[str, str]:
    """
    Header/footer body text fonts — same trio as paragraph ``base_font``.

    Omits complex-script (``font.cs``) and other locale-specific slots.
    """
    return _omit_none(
        {
            "name": _pick(fmt, "font", "font.latin", "effective.font.ascii"),
            "name_ascii": _pick(
                fmt, "font.latin", "font.ascii", "effective.font.ascii"
            ),
            "name_far_east": _pick(
                fmt, "font.ea", "font.eastAsia", "effective.font.eastAsia"
            ),
        }
    )


def _font_size(fmt: dict[str, Any]) -> str | None:
    raw = _pick(fmt, "size", "effective.size")
    return str(raw) if raw is not None else None


def _has_page_field(paragraph: dict[str, Any]) -> bool:
    for child in paragraph.get("children") or []:
        if child.get("type") == "instrText" and "page" in str(child.get("text", "")).lower():
            return True
    return False


def _has_page_field_in_tree(node: dict[str, Any] | None) -> bool:
    if not node:
        return False
    if node.get("type") == "paragraph" and _has_page_field(node):
        return True
    for child in node.get("children") or []:
        if _has_page_field_in_tree(child):
            return True
    return False


def _section_page_num_fmt(section_fmt: dict[str, Any]) -> str | None:
    raw = _scalar_from_fmt(section_fmt, "pageNumFmt")
    if raw is None:
        return None
    text = str(raw).strip().lower()
    if text in {"", "none"}:
        return None
    return str(raw).strip()


def _footer_visible_text(footer_node: dict[str, Any]) -> str:
    text = str(footer_node.get("text") or "").strip()
    if text:
        return text
    for child in footer_node.get("children") or []:
        child_text = str(child.get("text") or "").strip()
        if child_text:
            return child_text
    return ""


def _looks_like_page_number_placeholder(text: str) -> bool:
    text = (text or "").strip()
    return bool(text) and bool(re.fullmatch(r"[\dIVXLCDMivxlcdm]+", text))


def _footer_has_page_number_content(
    footer_node: dict[str, Any],
    *,
    section_fmt: dict[str, Any],
) -> bool:
    if _has_page_field_in_tree(footer_node):
        return True
    visible = _footer_visible_text(footer_node)
    if not visible:
        return False
    if _section_page_num_fmt(section_fmt):
        return True
    return _looks_like_page_number_placeholder(visible)


def _default_footer_page_alignment(
    *,
    slot_name: str | None,
    footer_node: dict[str, Any],
) -> str | None:
    footer_type = str((footer_node.get("format") or {}).get("type") or "").lower()
    if slot_name == "even" or footer_type == "even":
        return "right"
    return None


def _literal_text_from_paragraph(paragraph: dict[str, Any] | None) -> str:
    """Return visible text excluding PAGE (and other) field display runs."""
    if not paragraph:
        return ""
    parts: list[str] = []
    in_field = False
    for child in paragraph.get("children") or []:
        child_type = child.get("type")
        if child_type == "fieldChar":
            field_type = (child.get("format") or {}).get("fieldCharType")
            if field_type == "begin":
                in_field = True
            elif field_type == "end":
                in_field = False
            continue
        if child_type == "instrText":
            continue
        if child_type == "run" and not in_field:
            parts.append(str(child.get("text") or ""))
    return "".join(parts).strip()


def _build_margin(section_fmt: dict[str, Any]) -> dict[str, Any]:
    return _omit_none(
        {
            "top": section_fmt.get("marginTop"),
            "bottom": section_fmt.get("marginBottom"),
            "left": section_fmt.get("marginLeft"),
            "right": section_fmt.get("marginRight"),
        }
    )


def _build_paper(section_fmt: dict[str, Any]) -> dict[str, Any]:
    return _omit_none(
        {
            "size": section_fmt.get("paperSize"),
            "width": section_fmt.get("pageWidth"),
            "height": section_fmt.get("pageHeight"),
        }
    )


def _build_grid(section_fmt: dict[str, Any]) -> dict[str, Any]:
    return build_grid(section_fmt)


def _build_columns(section_fmt: dict[str, Any]) -> dict[str, Any]:
    return build_columns(section_fmt)


def _build_header_footer_layout(section_fmt: dict[str, Any]) -> dict[str, Any]:
    """Distances from body text to header/footer (officecli: marginHeader / marginFooter)."""
    return _omit_none(
        {
            "header_distance": (
                section_fmt.get("headerDistance")
                or section_fmt.get("header_distance")
                or section_fmt.get("marginHeader")
                or section_fmt.get("margin_header")
            ),
            "footer_distance": (
                section_fmt.get("footerDistance")
                or section_fmt.get("footer_distance")
                or section_fmt.get("marginFooter")
                or section_fmt.get("margin_footer")
            ),
        }
    )


def _header_footer_has_content(node: dict[str, Any], *, paragraph: dict[str, Any] | None) -> bool:
    if paragraph and _has_page_field(paragraph):
        return True
    text = (node.get("text") or (paragraph or {}).get("text") or "").strip()
    return bool(text)


def _first_slots_have_content(
    section_fmt: dict[str, Any],
    headers_by_path: dict[str, dict[str, Any]],
    footers_by_path: dict[str, dict[str, Any]],
    header_slots: dict[str, str | None],
    footer_slots: dict[str, str | None],
) -> bool:
    """True when a first-page header/footer slot has real content."""
    first_header_path = header_slots.get("first")
    if first_header_path:
        header_node = headers_by_path.get(first_header_path)
        if header_node and _header_slot(header_node).get("present"):
            return True
    first_footer_path = footer_slots.get("first")
    if first_footer_path:
        footer_node = footers_by_path.get(first_footer_path)
        if footer_node and _footer_slot(
            footer_node,
            section_fmt=section_fmt,
            slot_name="first",
        ).get("present"):
            return True
    return False


def _resolve_different_odd_even(
    section_fmt: dict[str, Any],
    document_fmt: dict[str, Any],
) -> bool:
    """
    Whether odd/even headers differ.

    Source of truth is ``w:evenAndOddHeaders`` (via document_fmt / settings.xml),
    not residual ``headerReference type=even`` parts. Those parts can exist while
    Word still has「奇偶页不同」off and ignores them.
    """
    if section_fmt.get("evenAndOddHeaders") is True:
        return True
    return document_fmt.get("evenAndOddHeaders") is True


def _resolve_different_first_page(
    section_fmt: dict[str, Any],
    headers_by_path: dict[str, dict[str, Any]],
    footers_by_path: dict[str, dict[str, Any]],
    header_slots: dict[str, str | None],
    footer_slots: dict[str, str | None],
) -> bool:
    title_page = section_fmt.get("titlePage") is True or str(
        section_fmt.get("titlePage", "")
    ).lower() == "true"
    if title_page:
        return True
    return _first_slots_have_content(
        section_fmt,
        headers_by_path,
        footers_by_path,
        header_slots,
        footer_slots,
    )


def _empty_header_footer_slot() -> dict[str, Any]:
    return {"present": False}


def _header_slot(header_node: dict[str, Any] | None) -> dict[str, Any]:
    if not header_node:
        return _empty_header_footer_slot()
    paragraph = _first_paragraph(header_node)
    if not _header_footer_has_content(header_node, paragraph=paragraph):
        return _empty_header_footer_slot()
    para_fmt = (paragraph or {}).get("format") or {}
    merged = {**(header_node.get("format") or {}), **para_fmt}
    text = (header_node.get("text") or (paragraph or {}).get("text") or "").strip()
    border_line = parse_border_line(para_fmt) or parse_border_line(merged)
    return _omit_none(
        {
            "present": True,
            "text": text or None,
            "align": _alignment(merged),
            **_font_names_triplet(merged),
            "size": _font_size(merged),
            "border_line": border_line if border_line is not None else None,
        }
    )


def _footer_slot(
    footer_node: dict[str, Any] | None,
    *,
    section_fmt: dict[str, Any],
    slot_name: str | None = None,
    doc_path: Path | str | None = None,
    section_index: int | None = None,
    section_count: int = 1,
) -> dict[str, Any]:
    if not footer_node:
        return _empty_header_footer_slot()
    if not _footer_has_page_number_content(footer_node, section_fmt=section_fmt):
        return _empty_header_footer_slot()
    paragraph = _first_paragraph(footer_node)
    para_fmt = (paragraph or {}).get("format") or {}
    merged = {**(footer_node.get("format") or {}), **para_fmt}
    if _has_page_field_in_tree(footer_node):
        text = _literal_text_from_paragraph(paragraph) if paragraph else ""
    else:
        text = _footer_visible_text(footer_node)
        if _looks_like_page_number_placeholder(text):
            text = ""
    page_number = dict(build_page_number(section_fmt, merged))
    if not page_number.get("alignment") and doc_path and section_index and slot_name:
        ooxml_align = alignment_for_section_footer_slot(
            doc_path,
            section_index,
            slot_name,
        )
        if ooxml_align:
            page_number["alignment"] = ooxml_align
    if not page_number.get("alignment"):
        default_align = _default_footer_page_alignment(
            slot_name=slot_name,
            footer_node=footer_node,
        )
        if default_align:
            page_number["alignment"] = default_align
    page_number = enrich_page_number_defaults(
        page_number,
        section_index=int(section_index or 1),
        section_count=max(1, int(section_count)),
        page_start_raw=section_fmt.get("pageStart"),
    )
    return _omit_none(
        {
            "present": True,
            "text": text or None,
            "page_number": page_number or None,
        }
    )


def _build_header(
    section_fmt: dict[str, Any],
    headers_by_path: dict[str, dict[str, Any]],
    *,
    document_fmt: dict[str, Any] | None = None,
    footers_by_path: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    document_fmt = document_fmt or {}
    footers_by_path = footers_by_path or {}
    slots = resolve_header_footer_refs(section_fmt, "header")
    footer_slots = resolve_header_footer_refs(section_fmt, "footer")
    different_odd_even = _resolve_different_odd_even(section_fmt, document_fmt)
    different_first_page = _resolve_different_first_page(
        section_fmt,
        headers_by_path,
        footers_by_path,
        slots,
        footer_slots,
    )

    header: dict[str, Any] = {
        "different_first_page": bool(different_first_page),
        "different_odd_even": bool(different_odd_even),
    }
    for slot_name in ("primary", "first", "even"):
        header_path = slots.get(slot_name)
        if slot_name == "first" and not different_first_page:
            header[slot_name] = _empty_header_footer_slot()
            continue
        if slot_name == "even" and not different_odd_even:
            header[slot_name] = _empty_header_footer_slot()
            continue
        if not header_path:
            header[slot_name] = _empty_header_footer_slot()
            continue
        header[slot_name] = _header_slot(headers_by_path.get(header_path))
    return header


def _build_footer(
    section_fmt: dict[str, Any],
    footers_by_path: dict[str, dict[str, Any]],
    *,
    document_fmt: dict[str, Any] | None = None,
    headers_by_path: dict[str, dict[str, Any]] | None = None,
    doc_path: Path | str | None = None,
    section_index: int | None = None,
    section_count: int = 1,
) -> dict[str, Any]:
    document_fmt = document_fmt or {}
    headers_by_path = headers_by_path or {}
    slots = resolve_header_footer_refs(section_fmt, "footer")
    header_slots = resolve_header_footer_refs(section_fmt, "header")
    different_odd_even = _resolve_different_odd_even(section_fmt, document_fmt)
    different_first_page = _resolve_different_first_page(
        section_fmt,
        headers_by_path,
        footers_by_path,
        header_slots,
        slots,
    )

    footer: dict[str, Any] = {
        "different_first_page": bool(different_first_page),
        "different_odd_even": bool(different_odd_even),
    }
    for slot_name in ("primary", "first", "even"):
        footer_path = slots.get(slot_name)
        if slot_name == "first" and not different_first_page:
            footer[slot_name] = _empty_header_footer_slot()
            continue
        if slot_name == "even" and not different_odd_even:
            footer[slot_name] = _empty_header_footer_slot()
            continue
        if not footer_path:
            footer[slot_name] = _empty_header_footer_slot()
            continue
        footer[slot_name] = _footer_slot(
            footers_by_path.get(footer_path),
            section_fmt=section_fmt,
            slot_name=slot_name,
            doc_path=doc_path,
            section_index=section_index,
            section_count=section_count,
        )
    return footer


@dataclass
class PageFormatInfo:
    """
    One section's page setup (flat ``page_format`` for read/write).

    ``page_format`` groups (field names aligned with ref categories):

    - margin, paper, grid, columns
    - header_footer_layout
    - header, footer
    """

    section_index: int
    path: str
    page_format: dict[str, Any] = field(default_factory=dict)
    kind: str = "section"

    def to_dict(self) -> dict[str, Any]:
        return {
            "section_index": self.section_index,
            "path": self.path,
            "kind": self.kind,
            "page_format": self.page_format,
        }


class WordPageReader:
    """
    Read page/section formatting from a .docx via officecli.

    Output uses officecli native units (e.g. ``2.5cm``, ``11pt``) and flat dicts
    so the same structure can be passed to a writer.
    """

    SECTION_SELECTOR = "section"
    PAGEBREAK_SELECTOR = "pagebreak"
    HEADER_DEPTH = 3
    FOOTER_DEPTH = 6

    def __init__(self, doc_path: str | Path, officecli: str = "officecli") -> None:
        self.doc_path = Path(doc_path).resolve()
        self.officecli = officecli
        if not self.doc_path.is_file():
            raise FileNotFoundError(f"Document not found: {self.doc_path}")

    def _load_document_format(self) -> dict[str, Any]:
        node = get_element(self.doc_path, "/", officecli=self.officecli)
        fmt = dict((node or {}).get("format") or {})
        # officecli often omits this; settings.xml is the Word UI source of truth.
        fmt["evenAndOddHeaders"] = read_even_and_odd_headers(self.doc_path)
        return fmt

    def _load_headers_by_path(self) -> dict[str, dict[str, Any]]:
        headers: dict[str, dict[str, Any]] = {}
        for node in query_elements(self.doc_path, "header", officecli=self.officecli):
            path = node.get("path", "")
            headers[path] = (
                get_element(
                    self.doc_path,
                    path,
                    officecli=self.officecli,
                    depth=self.HEADER_DEPTH,
                )
                or node
            )
        return headers

    def _load_footers_by_path(self) -> dict[str, dict[str, Any]]:
        footers: dict[str, dict[str, Any]] = {}
        for node in query_elements(self.doc_path, "footer", officecli=self.officecli):
            path = node.get("path", "")
            footers[path] = (
                get_element(
                    self.doc_path,
                    path,
                    officecli=self.officecli,
                    depth=self.FOOTER_DEPTH,
                )
                or node
            )
        return footers

    def _build_page_format(
        self,
        section_fmt: dict[str, Any],
        *,
        document_fmt: dict[str, Any],
        headers_by_path: dict[str, dict[str, Any]],
        footers_by_path: dict[str, dict[str, Any]],
        section_index: int,
        section_count: int,
    ) -> dict[str, Any]:
        footer = _build_footer(
            section_fmt,
            footers_by_path,
            document_fmt=document_fmt,
            headers_by_path=headers_by_path,
            doc_path=self.doc_path,
            section_index=section_index,
            section_count=section_count,
        ) or None
        return _omit_none(
            {
                "margin": _build_margin(section_fmt) or None,
                "paper": _build_paper(section_fmt) or None,
                "grid": _build_grid(section_fmt),
                "columns": _build_columns(section_fmt),
                "header_footer_layout": _build_header_footer_layout(section_fmt) or None,
                "header": _build_header(
                    section_fmt,
                    headers_by_path,
                    document_fmt=document_fmt,
                    footers_by_path=footers_by_path,
                )
                or None,
                "footer": footer,
                "section_type": section_fmt.get("type"),
                "page_start": resolve_section_page_start(section_fmt, footer),
                "page_num_fmt": section_fmt.get("pageNumFmt"),
            }
        )

    def _assemble_section(
        self,
        node: dict[str, Any],
        *,
        document_fmt: dict[str, Any],
        headers_by_path: dict[str, dict[str, Any]],
        footers_by_path: dict[str, dict[str, Any]],
        section_count: int,
    ) -> PageFormatInfo:
        path = node.get("path", "")
        section_index = _path_index(path, "section") or 1
        section_fmt = dict(node.get("format") or {})
        return PageFormatInfo(
            section_index=section_index,
            path=path,
            page_format=self._build_page_format(
                section_fmt,
                document_fmt=document_fmt,
                headers_by_path=headers_by_path,
                footers_by_path=footers_by_path,
                section_index=section_index,
                section_count=section_count,
            ),
        )

    def read_sections(self) -> list[PageFormatInfo]:
        """Read all sections with margin, header, footer, etc. in one pass."""
        document_fmt = self._load_document_format()
        headers_by_path = self._load_headers_by_path()
        footers_by_path = self._load_footers_by_path()
        nodes = query_elements(
            self.doc_path,
            self.SECTION_SELECTOR,
            officecli=self.officecli,
        )
        section_count = len(nodes)
        results = [
            self._assemble_section(
                node,
                document_fmt=document_fmt,
                headers_by_path=headers_by_path,
                footers_by_path=footers_by_path,
                section_count=section_count,
            )
            for node in nodes
        ]
        results.sort(key=lambda item: item.section_index)
        return results

    def read_section_at(self, index: int = 1) -> PageFormatInfo | None:
        """Read one section by 1-based index."""
        node = get_element(
            self.doc_path,
            f"/section[{index}]",
            officecli=self.officecli,
        )
        if not node:
            return None
        section_count = len(
            query_elements(
                self.doc_path,
                self.SECTION_SELECTOR,
                officecli=self.officecli,
            )
        )
        return self._assemble_section(
            node,
            document_fmt=self._load_document_format(),
            headers_by_path=self._load_headers_by_path(),
            footers_by_path=self._load_footers_by_path(),
            section_count=max(1, section_count),
        )

    def read_page_breaks(self) -> list[PageFormatInfo]:
        nodes = query_elements(
            self.doc_path,
            self.PAGEBREAK_SELECTOR,
            officecli=self.officecli,
        )
        return [
            PageFormatInfo(
                section_index=0,
                path=node.get("path", ""),
                kind=node.get("type", "pagebreak"),
                page_format={"break": dict(node.get("format") or {})},
            )
            for node in nodes
        ]

    def read_stats(self) -> dict[str, Any]:
        payload = run_officecli(
            "view",
            str(self.doc_path),
            "stats",
            "--json",
            officecli=self.officecli,
        )
        return dict(payload.get("data") or {})

    def read_all(self) -> dict[str, Any]:
        return {
            "sections": [item.to_dict() for item in self.read_sections()],
            "page_breaks": [item.to_dict() for item in self.read_page_breaks()],
            "stats": self.read_stats(),
        }
