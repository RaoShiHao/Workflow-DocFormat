"""Read footer paragraph alignment from OOXML (SDT page numbers; path index != part name)."""

from __future__ import annotations

import re
import zipfile
from functools import lru_cache
from pathlib import Path
from xml.etree import ElementTree as ET

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_JC_TAG = f"{{{_W_NS}}}jc"
_SLOT_TO_REF_TYPE = {"primary": "default", "first": "first", "even": "even"}


def _normalize_part_path(target: str) -> str:
    text = str(target or "").strip().replace("\\", "/")
    if not text:
        return ""
    if not text.startswith("word/"):
        text = f"word/{text.lstrip('/')}"
    return text


def _load_document_rels(archive: zipfile.ZipFile) -> dict[str, str]:
    rels: dict[str, str] = {}
    try:
        text = archive.read("word/_rels/document.xml.rels").decode()
    except KeyError:
        return rels
    for match in re.finditer(r'Id="([^"]+)"[^>]+Target="([^"]+)"', text):
        rels[match.group(1)] = match.group(2)
    return rels


def _footer_ref_types_from_sectpr(sectpr_xml: str, rels: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for match in re.finditer(
        r'footerReference w:type="([^"]+)" r:id="([^"]+)"',
        sectpr_xml,
    ):
        ref_type = match.group(1)
        part = _normalize_part_path(rels.get(match.group(2), ""))
        if part:
            out[ref_type] = part
    return out


@lru_cache(maxsize=32)
def _footer_parts_by_section_cache(doc_key: tuple[str, float, int]) -> tuple[dict[str, str], ...]:
    doc_path = Path(doc_key[0])
    with zipfile.ZipFile(doc_path) as archive:
        rels = _load_document_rels(archive)
        document_xml = archive.read("word/document.xml").decode()
    sections = re.split(r"(?=<w:sectPr)", document_xml)
    result: list[dict[str, str]] = []
    for block in sections[1:]:
        result.append(_footer_ref_types_from_sectpr(block, rels))
    return tuple(result)


def footer_parts_by_section(doc_path: Path | str) -> list[dict[str, str]]:
    """Per-section footer slot -> package part (e.g. ``word/footer4.xml``)."""
    path = Path(doc_path).resolve()
    stat = path.stat()
    cached = _footer_parts_by_section_cache((str(path), stat.st_mtime, stat.st_size))
    return [dict(item) for item in cached]


def footer_part_path_for_slot(
    doc_path: Path | str,
    section_index: int,
    slot_name: str,
) -> str | None:
    ref_type = _SLOT_TO_REF_TYPE.get(slot_name)
    if not ref_type or section_index < 1:
        return None
    sections = footer_parts_by_section(doc_path)
    if section_index > len(sections):
        return None
    return sections[section_index - 1].get(ref_type)


def alignment_from_footer_part_xml(doc_path: Path | str, part_path: str) -> str | None:
    """Return first ``w:jc/@w:val`` from a footer part (e.g. ``word/footer4.xml``)."""
    part_path = _normalize_part_path(part_path)
    if not part_path:
        return None
    try:
        with zipfile.ZipFile(Path(doc_path)) as archive:
            if part_path not in archive.namelist():
                return None
            root = ET.fromstring(archive.read(part_path))
    except (OSError, zipfile.BadZipFile, ET.ParseError):
        return None

    for jc in root.iter(_JC_TAG):
        val = jc.get(f"{{{_W_NS}}}val") or jc.get("val")
        text = str(val or "").strip().lower()
        if text:
            return text
    return None


def alignment_for_section_footer_slot(
    doc_path: Path | str,
    section_index: int,
    slot_name: str,
) -> str | None:
    """Alignment for one section footer slot using OOXML ``footerReference`` targets."""
    part = footer_part_path_for_slot(doc_path, section_index, slot_name)
    if not part:
        return None
    return alignment_from_footer_part_xml(doc_path, part)
