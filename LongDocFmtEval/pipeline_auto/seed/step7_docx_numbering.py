"""Insert heading numbering prefixes into docx copies (no rerender).

Locates headings by **effective outline level** on each body paragraph:
paragraph ``w:outlineLvl`` when present, otherwise the linked style's ``outlineLvl``.
Prefixes are computed in strict document order for the target docx only (golden and
template are numbered independently).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from lxml import etree

from LongDocFmtEval.pipeline_auto.seed.build_step6_pairs import (
    W,
    _body_paragraphs,
    _dumps,
    _para_plain_text,
    _parse,
    _paragraph_style_id,
    _qn,
    _read_docx_parts,
    _write_docx_parts,
)
from LongDocFmtEval.pipeline_auto.seed.heading_numbering import (
    apply_prefix_to_text,
    compute_prefixes_for_heading_levels,
    load_schemes,
)

_MARKER_RE = re.compile(r"^\{([A-Za-z][A-Za-z0-9_]*)\}")


def _outline_level_from_p_pr(p_pr: etree._Element | None) -> int | None:
    if p_pr is None:
        return None
    ol = p_pr.find(_qn("w:outlineLvl"))
    if ol is None:
        return None
    try:
        return int(ol.get(f"{{{W}}}val")) + 1
    except (TypeError, ValueError):
        return None


def _build_style_outline_index(styles_xml: bytes) -> dict[str, int]:
    """styleId → heading level (1..9) from style outlineLvl, following basedOn."""
    root = _parse(styles_xml)
    by_id: dict[str, etree._Element] = {}
    for style in root.findall(_qn("w:style")):
        if style.get(f"{{{W}}}type") != "paragraph":
            continue
        sid = style.get(f"{{{W}}}styleId")
        if sid:
            by_id[sid] = style

    cache: dict[str, int | None] = {}

    def resolve(sid: str, seen: set[str]) -> int | None:
        if sid in cache:
            return cache[sid]
        if sid in seen:
            return None
        seen.add(sid)
        style_el = by_id.get(sid)
        if style_el is None:
            cache[sid] = None
            return None
        p_pr = style_el.find(_qn("w:pPr"))
        lvl = _outline_level_from_p_pr(p_pr)
        if lvl is not None:
            cache[sid] = lvl
            return lvl
        based = style_el.find(_qn("w:basedOn"))
        parent = based.get(f"{{{W}}}val") if based is not None else None
        parent_lvl = resolve(parent, seen) if parent else None
        cache[sid] = parent_lvl
        return parent_lvl

    out: dict[str, int] = {}
    for sid in by_id:
        lvl = resolve(sid, set())
        if lvl is not None:
            out[sid] = int(lvl)
    return out


def effective_outline_level(
    p: etree._Element,
    *,
    style_outline: dict[str, int],
) -> tuple[int | None, str]:
    """Return (heading_level, source) where source is ``pPr`` or ``style``."""
    p_pr = p.find(_qn("w:pPr"))
    lvl = _outline_level_from_p_pr(p_pr)
    if lvl is not None:
        return lvl, "pPr"
    sid = _paragraph_style_id(p)
    if sid and sid in style_outline:
        return int(style_outline[sid]), "style"
    return None, ""


def collect_outline_heading_paragraphs(docx_path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Scan body paragraphs with an effective outline level, in document order."""
    parts = _read_docx_parts(docx_path)
    root = _parse(parts["word/document.xml"])
    style_outline = _build_style_outline_index(parts.get("word/styles.xml") or b"<styles/>")
    rows: list[dict[str, Any]] = []
    via_p_pr = 0
    via_style = 0
    for i, p in enumerate(_body_paragraphs(root), start=1):
        lvl, via = effective_outline_level(p, style_outline=style_outline)
        if lvl is None:
            continue
        if via == "pPr":
            via_p_pr += 1
        elif via == "style":
            via_style += 1
        rows.append({
            "para_index": i,
            "heading_level": int(lvl),
            "via": via,
            "style_id": _paragraph_style_id(p),
            "text_preview": _para_plain_text(p)[:80],
        })
    report = {
        "heading_paragraphs": len(rows),
        "via_pPr": via_p_pr,
        "via_style": via_style,
    }
    return rows, report


def plan_outline_insertions(
    docx_path: Path,
    scheme_id: str,
    *,
    schemes: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build insertions from docx outline levels + scheme counters."""
    schemes = schemes or load_schemes()
    headings, scan_report = collect_outline_heading_paragraphs(docx_path)
    levels = [int(h["heading_level"]) for h in headings]
    prefixes = compute_prefixes_for_heading_levels(levels, scheme_id, schemes=schemes)
    insertions: list[dict[str, Any]] = []
    for heading, prefix in zip(headings, prefixes):
        if not str(prefix or "").strip():
            continue
        insertions.append({
            **heading,
            "prefix": prefix,
        })
    report = {
        "scheme_id": scheme_id,
        "locator": "outlineLvl",
        "prefix_count": len(insertions),
        **scan_report,
    }
    return insertions, report


def _set_paragraph_plain(p, new_plain: str) -> None:
    texts = list(p.iter(_qn("w:t")))
    if not texts:
        return
    texts[0].text = new_plain
    for t in texts[1:]:
        t.text = ""


def _prepend_prefix_to_plain(plain: str, prefix: str) -> str | None:
    if not prefix:
        return None
    body = str(plain or "")
    pref = str(prefix)
    if not body.strip():
        return pref.strip()
    m = _MARKER_RE.match(body)
    if m:
        rest = body[m.end() :]
        rest_stripped = rest.lstrip()
        if rest_stripped.startswith(pref.strip()):
            return None
        new_rest = apply_prefix_to_text(rest_stripped, pref)
        return body[: m.end()] + new_rest
    stripped = body.lstrip()
    if stripped.startswith(pref.strip()):
        return None
    return apply_prefix_to_text(body, pref)


def insert_heading_prefixes_in_docx(
    docx_path: Path,
    insertions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Prepend numbering prefixes at mapped paragraph indices."""
    docx_path = docx_path.resolve()
    parts = _read_docx_parts(docx_path)
    root = _parse(parts["word/document.xml"])
    paras = _body_paragraphs(root)
    applied = 0
    skipped = 0
    errors: list[str] = []

    for ins in insertions:
        idx = int(ins.get("para_index") or 0)
        prefix = str(ins.get("prefix") or "")
        if idx < 1 or idx > len(paras):
            errors.append(f"para_index {idx} out of range")
            continue
        p = paras[idx - 1]
        plain = _para_plain_text(p)
        new_plain = _prepend_prefix_to_plain(plain, prefix)
        if new_plain is None:
            skipped += 1
            continue
        _set_paragraph_plain(p, new_plain)
        applied += 1

    parts["word/document.xml"] = _dumps(root)
    _write_docx_parts(docx_path, parts)
    return {"applied": applied, "skipped": skipped, "errors": errors}


def apply_document_numbering(
    docx_path: Path,
    *,
    scheme_id: str,
    outline_source: Path | None = None,
) -> dict[str, Any]:
    """Insert heading prefixes using outline levels.

    By default scan ``docx_path``. Pass ``outline_source`` when the target has
    lost heading styles (e.g. plain ``input.docx``) but shares paragraph order
    with a styled sibling (step6 ``document.docx``).
    """
    source = Path(outline_source) if outline_source else docx_path
    insertions, plan_report = plan_outline_insertions(source, scheme_id)
    patch_report = insert_heading_prefixes_in_docx(docx_path, insertions)
    report: dict[str, Any] = {
        "mode": "insert_prefix_outline",
        "insertions": len(insertions),
        **plan_report,
        **patch_report,
    }
    if outline_source is not None:
        report["outline_source"] = str(Path(outline_source)).replace("\\", "/")
    return report
