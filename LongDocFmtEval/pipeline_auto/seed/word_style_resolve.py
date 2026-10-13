"""Pipeline-owned Word heading/title styles (do not overwrite built-ins).

When a document already has built-in ``标题 4`` (styleId ``4``) or ``heading 4``, we still
inject our own paragraph style for partial init / delta — but under an explicit alternate id
such as ``Heading4a`` (display ``heading 4a``) instead of letting Word/officecli auto-suffix
to ``Heading41`` / ``标题 41``.

Canonical ids (``Heading1``…``Heading4``, ``Title``, ``Subtitle``) are used when the
styleId slot is free. Otherwise suffix ``a``, then ``b``, …
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import Any

from lxml import etree

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NSMAP = {"w": W}

HEADING_CANONICAL_BY_OUTLINE: dict[int, str] = {
    0: "Heading1",
    1: "Heading2",
    2: "Heading3",
    3: "Heading4",
    4: "Heading5",
    5: "Heading6",
}
TITLE_STYLES = ("Title", "Subtitle")
ALT_SUFFIXES = ("a", "b", "c", "d", "e")


def _qn(tag: str) -> str:
    prefix, local = tag.split(":")
    return f"{{{NSMAP[prefix]}}}{local}"


def _parse(xml: bytes) -> etree._Element:
    return etree.fromstring(xml)


def _style_name(style_el: etree._Element) -> str:
    name_el = style_el.find(_qn("w:name"))
    if name_el is None:
        return ""
    return str(name_el.get(f"{{{W}}}val") or "").strip()


def _style_outline(style_el: etree._Element) -> int | None:
    p_pr = style_el.find(_qn("w:pPr"))
    if p_pr is None:
        return None
    outline = p_pr.find(_qn("w:outlineLvl"))
    if outline is None:
        return None
    try:
        return int(outline.get(f"{{{W}}}val") or "")
    except (TypeError, ValueError):
        return None


def scan_paragraph_styles(styles_xml: bytes) -> list[dict[str, Any]]:
    root = _parse(styles_xml)
    out: list[dict[str, Any]] = []
    for style_el in root.findall(_qn("w:style")):
        if str(style_el.get(f"{{{W}}}type") or "") != "paragraph":
            continue
        sid = str(style_el.get(f"{{{W}}}styleId") or "").strip()
        if not sid:
            continue
        out.append({
            "style_id": sid,
            "name": _style_name(style_el),
            "outline": _style_outline(style_el),
            "custom": style_el.find(_qn("w:customStyle")) is not None,
        })
    return out


def existing_style_ids(styles_xml: bytes) -> set[str]:
    root = _parse(styles_xml)
    return {
        str(s.get(f"{{{W}}}styleId") or "").strip()
        for s in root.findall(_qn("w:style"))
        if str(s.get(f"{{{W}}}styleId") or "").strip()
    }


def outline_occupied(styles_xml: bytes) -> dict[int, str]:
    out: dict[int, str] = {}
    for s in scan_paragraph_styles(styles_xml):
        ol = s.get("outline")
        if ol is None:
            continue
        out.setdefault(int(ol), s["style_id"])
    return out


def _heading_level_from_canonical(canonical: str) -> int | None:
    m = re.fullmatch(r"Heading([1-6])", str(canonical or ""))
    if not m:
        return None
    return int(m.group(1))


def _display_name_conflict(styles: list[dict[str, Any]], base_name: str) -> bool:
    base = str(base_name or "").strip().lower()
    if not base:
        return False
    for s in styles:
        name = str(s.get("name") or "").strip().lower()
        if name == base:
            return True
    return False


def needs_pipeline_alt(
    canonical: str,
    *,
    existing_ids: set[str],
    styles: list[dict[str, Any]],
    outline_map: dict[int, str],
    outline: int | None,
) -> bool:
    """True when canonical id is taken or a same-level built-in heading already exists."""
    if canonical in existing_ids:
        return True
    if outline is None:
        return _display_name_conflict(styles, canonical)
    ol = int(outline)
    occupant = outline_map.get(ol)
    if occupant and occupant != canonical:
        return True
    level = _heading_level_from_canonical(canonical)
    if level is not None:
        base = f"heading {level}"
        if _display_name_conflict(styles, base):
            return True
    return False


def pick_pipeline_style_id(
    canonical: str,
    existing_ids: set[str],
    *,
    force_alt: bool = False,
) -> str:
    """Return a free styleId: canonical, or ``{canonical}a`` / ``b`` …"""
    canon = str(canonical or "").strip()
    if not canon:
        return canon
    candidates = [canon] if not force_alt else []
    for suf in ALT_SUFFIXES:
        candidates.append(f"{canon}{suf}")
    for cand in candidates:
        if cand not in existing_ids:
            return cand
    n = 2
    while True:
        cand = f"{canon}_alt{n}"
        if cand not in existing_ids:
            return cand
        n += 1


def pipeline_display_name(base_name: str, actual_id: str, canonical: str) -> str:
    if actual_id == canonical:
        return base_name
    if actual_id.startswith(canonical) and len(actual_id) > len(canonical):
        suffix = actual_id[len(canonical):]
        return f"{base_name}{suffix}"
    return f"{base_name} alt"


def allocate_pipeline_style(
    canonical: str,
    base_display_name: str,
    *,
    styles_xml: bytes,
    outline: int | None = None,
) -> tuple[str, str, bool]:
    """Return ``(actual_style_id, display_name, is_alt)`` for injection / officecli set."""
    styles = scan_paragraph_styles(styles_xml)
    existing = existing_style_ids(styles_xml)
    outline_map = outline_occupied(styles_xml)
    alt = needs_pipeline_alt(
        canonical,
        existing_ids=existing,
        styles=styles,
        outline_map=outline_map,
        outline=outline,
    )
    actual = pick_pipeline_style_id(canonical, existing, force_alt=alt)
    display = pipeline_display_name(base_display_name, actual, canonical)
    return actual, display, actual != canonical


def build_canonical_resolution(styles_xml: bytes) -> dict[str, str]:
    """Map canonical ids → pipeline styleIds already present in the document."""
    existing = existing_style_ids(styles_xml)
    resolution: dict[str, str] = {}
    for canon in list(HEADING_CANONICAL_BY_OUTLINE.values()) + list(TITLE_STYLES):
        if canon in existing:
            resolution[canon] = canon
            continue
        alt = f"{canon}a"
        if alt in existing:
            resolution[canon] = alt
            continue
        for suf in ALT_SUFFIXES[1:]:
            cand = f"{canon}{suf}"
            if cand in existing:
                resolution[canon] = cand
                break
    return resolution


def resolve_canonical_style(canonical: str, resolution: dict[str, str]) -> str:
    key = str(canonical or "").strip()
    if not key:
        return key
    return str(resolution.get(key) or key)


def read_docx_parts(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path, "r") as zf:
        return {name: zf.read(name) for name in zf.namelist()}


def resolve_pipeline_write_style(docx: Path, canonical: str) -> str:
    """Style id to use for ``/styles/{id}`` writes (never auto-suffix ``Heading41``).

    For arbitrary pipeline roles (``Para*`` etc.): if the id already exists in the
    document, keep it. Do **not** invent ``ParaBodya`` — that path is only for
    Heading/Title canonicals colliding with built-ins.
    """
    canon = str(canonical or "").strip()
    if not canon:
        return canon
    try:
        parts = read_docx_parts(Path(docx).resolve())
    except Exception:  # noqa: BLE001
        return canon
    styles_xml = parts.get("word/styles.xml") or b""
    existing = existing_style_ids(styles_xml)
    if canon in existing:
        return canon
    resolution = build_canonical_resolution(styles_xml)
    if canon in resolution:
        return resolution[canon]
    # Only allocate alternates for heading/title slots; other ids pass through.
    heading_canons = set(HEADING_CANONICAL_BY_OUTLINE.values()) | set(TITLE_STYLES)
    if canon not in heading_canons and not (
        canon[:-1] in heading_canons and canon[-1:] in ALT_SUFFIXES
    ):
        return canon
    outline = None
    level = _heading_level_from_canonical(canon)
    if level is not None:
        outline = level - 1
    actual, _, _ = allocate_pipeline_style(
        canon,
        canon.lower() if canon in TITLE_STYLES else f"heading {level or ''}".strip(),
        styles_xml=styles_xml,
        outline=outline,
    )
    return actual
