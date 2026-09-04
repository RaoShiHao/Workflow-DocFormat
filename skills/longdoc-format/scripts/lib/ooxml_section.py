"""Resolve body paragraph / picture paths to 1-based Word section index via OOXML."""

from __future__ import annotations

import re
import zipfile
from functools import lru_cache
from pathlib import Path
from xml.etree import ElementTree as ET

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_W14_NS = "http://schemas.microsoft.com/office/word/2010/wordml"
_NS = {"w": _W_NS}
_PARA_ID_ATTR = f"{{{_W14_NS}}}paraId"

_PARA_ID_PATH_RE = re.compile(r"/body/p\[@paraId=([^\]]+)\]")


@lru_cache(maxsize=8)
def _load_body_section_breaks(doc_path: str) -> tuple[int, ...]:
    """
    Paragraph indices (1-based) after which a new section starts.

    Derived from ``w:pPr/w:sectPr`` in document order. The trailing body
    ``w:sectPr`` does not add an entry (it closes the last section).
    """
    path = Path(doc_path)
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))

    body = root.find("w:body", _NS)
    if body is None:
        return ()

    breaks: list[int] = []
    para_index = 0
    for child in body:
        tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if tag != "p":
            continue
        para_index += 1
        p_pr = child.find("w:pPr", _NS)
        if p_pr is not None and p_pr.find("w:sectPr", _NS) is not None:
            breaks.append(para_index)
    return tuple(breaks)


@lru_cache(maxsize=8)
def _load_para_id_to_index(doc_path: str) -> dict[str, int]:
    """Map ``w14:paraId`` → 1-based paragraph index in document body."""
    path = Path(doc_path)
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))

    mapping: dict[str, int] = {}
    para_index = 0
    for p in root.findall(".//w:body/w:p", _NS):
        para_index += 1
        para_id = p.get(_PARA_ID_ATTR)
        if para_id:
            mapping[para_id.upper()] = para_index
            mapping[para_id] = para_index
    return mapping


def para_id_from_path(path: str) -> str | None:
    match = _PARA_ID_PATH_RE.search(path)
    return match.group(1) if match else None


def paragraph_index_for_path(doc_path: str | Path, path: str) -> int | None:
    """1-based body paragraph index for a paragraph or picture path."""
    para_id = para_id_from_path(path)
    if not para_id:
        return None
    mapping = _load_para_id_to_index(str(Path(doc_path).resolve()))
    return mapping.get(para_id) or mapping.get(para_id.upper())


def section_index_for_paragraph(para_index: int, section_breaks: tuple[int, ...]) -> int:
    """Section number (1-based) containing ``para_index``."""
    if para_index < 1:
        return 1
    section = 1
    for break_after in section_breaks:
        if para_index > break_after:
            section += 1
        else:
            break
    return section


def section_index_for_picture_path(doc_path: str | Path, picture_path: str) -> int:
    """
    1-based section index for a picture at ``/body/p[@paraId=…]/r[N]``.

    Uses OOXML section breaks (same model as Word ``Sections(n)``).
    """
    doc_path = str(Path(doc_path).resolve())
    para_index = paragraph_index_for_path(doc_path, picture_path)
    if para_index is None:
        return 1
    breaks = _load_body_section_breaks(doc_path)
    return section_index_for_paragraph(para_index, breaks)


def table_section_indices_from_body(body: ET.Element) -> tuple[int, ...]:
    """1-based section index for each ``w:tbl`` in document order.

    A paragraph ``w:sectPr`` closes the current section; the next block
    (including a following table) belongs to the next section.
    """
    sections: list[int] = []
    section = 1
    for child in list(body):
        tag = _w_tag(child)
        if tag == "p":
            p_pr = child.find("w:pPr", _NS)
            if p_pr is not None and p_pr.find("w:sectPr", _NS) is not None:
                section += 1
            continue
        if tag == "tbl":
            sections.append(section)
            continue
        if tag == "sectPr":
            break
    return tuple(sections)


@lru_cache(maxsize=8)
def _load_table_section_indices(doc_path: str) -> tuple[int, ...]:
    path = Path(doc_path)
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    body = root.find("w:body", _NS)
    if body is None:
        return ()
    return table_section_indices_from_body(body)


_TABLE_PATH_RE = re.compile(r"/(?:tbl|table)\[(\d+)\]", re.I)


def section_index_for_table_path(doc_path: str | Path, table_path: str) -> int:
    """1-based section index for ``/body/tbl[N]`` / ``/body/table[N]``."""
    match = _TABLE_PATH_RE.search(str(table_path or ""))
    idx = int(match.group(1)) if match else 1
    indices = _load_table_section_indices(str(Path(doc_path).resolve()))
    if 1 <= idx <= len(indices):
        return indices[idx - 1]
    return 1


def _w_tag(elem: ET.Element) -> str:
    return elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag


def _elem_text(elem: ET.Element) -> str:
    parts: list[str] = []
    for node in elem.iter(f"{{{_W_NS}}}t"):
        if node.text:
            parts.append(node.text)
    return "".join(parts).strip()


def _table_preview_text(
    tbl: ET.Element,
    *,
    max_rows: int = 8,
    max_cols: int = 6,
    max_cell: int = 80,
) -> str:
    """First rows of a table as tab-separated lines (one block for the section preview)."""
    lines: list[str] = []
    for tr in tbl.findall("w:tr", _NS)[:max_rows]:
        cells: list[str] = []
        for tc in tr.findall("w:tc", _NS)[:max_cols]:
            text = _elem_text(tc)[:max_cell].strip()
            if text:
                cells.append(text)
        if cells:
            lines.append("\t".join(cells))
    return "\n".join(lines)


@lru_cache(maxsize=8)
def load_section_preview_texts(
    doc_path: str,
    max_blocks: int = 8,
    max_para_chars: int = 500,
    max_section_chars: int = 1600,
) -> tuple[str, ...]:
    """First body blocks of each section (paragraphs + compact tables), for assignment cues."""
    path = Path(doc_path)
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    body = root.find("w:body", _NS)
    if body is None:
        return ()

    chunks: list[list[str]] = [[]]
    current = 0

    def _add(text: str) -> None:
        text = (text or "").strip()
        if not text or len(chunks[current]) >= max_blocks:
            return
        chunks[current].append(text)

    for child in list(body):
        tag = _w_tag(child)
        if tag == "p":
            _add(_elem_text(child)[:max_para_chars])
            p_pr = child.find("w:pPr", _NS)
            if p_pr is not None and p_pr.find("w:sectPr", _NS) is not None:
                chunks.append([])
                current = len(chunks) - 1
            continue
        if tag == "tbl":
            _add(_table_preview_text(tbl=child))
            continue
        if tag == "sectPr":
            break

    if chunks and not chunks[-1] and len(chunks) > 1:
        chunks.pop()
    out: list[str] = []
    for parts in chunks:
        text = "\n".join(parts).strip()
        if len(text) > max_section_chars:
            text = text[:max_section_chars].rstrip()
        out.append(text)
    return tuple(out)


def section_preview_text(doc_path: str | Path, section_index: int) -> str:
    texts = load_section_preview_texts(str(Path(doc_path).resolve()))
    if 1 <= int(section_index) <= len(texts):
        return texts[int(section_index) - 1]
    return ""
