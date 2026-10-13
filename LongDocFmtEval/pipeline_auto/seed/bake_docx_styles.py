"""Bake custom paragraph styles into Normal + direct-format deltas (OOXML).

For each paragraph whose ``w:pStyle`` is a custom style (typically ``Para*``):
  1. Resolve effective pPr/rPr via ``basedOn`` chain
  2. Diff against Normal's effective props
  3. Set ``pStyle=Normal`` and write only the delta as direct formatting
  4. Optionally strip ``{ParaXxx}`` markers from visible text

Fast path: pure lxml, no officecli. Suitable for step6 ``t_template_direct.docx``.
"""
from __future__ import annotations

import copy
import re
import shutil
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any

from lxml import etree

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NSMAP = {"w": W}

_MARKER_RE = re.compile(r"\{Para[A-Za-z0-9_]+\}")

# Built-in / leave-as-is paragraph styles (do not bake)
_KEEP_STYLES = {
    "Normal", "normal",
    "Title", "Subtitle",
    "Heading1", "Heading2", "Heading3", "Heading4", "Heading5", "Heading6",
    "heading 1", "heading 2", "heading 3", "heading 4",
    "TOC1", "TOC2", "TOC3", "TOC4",
    "Caption", "Quote", "IntenseQuote",
    "ListParagraph", "NoSpacing",
    "TableParagraph", "BodyText",
}


def _qn(tag: str) -> str:
    prefix, local = tag.split(":")
    return f"{{{NSMAP[prefix]}}}{local}"


def _local(tag: str | None) -> str:
    if not tag:
        return ""
    if tag.startswith("{"):
        return tag.rsplit("}", 1)[-1]
    return tag


def _read_parts(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path, "r") as zf:
        return {name: zf.read(name) for name in zf.namelist()}


def _write_parts(path: Path, parts: dict[str, bytes]) -> None:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in parts.items():
            zf.writestr(name, data)
    path.write_bytes(buf.getvalue())


def _parse(xml: bytes) -> etree._Element:
    return etree.fromstring(xml)


def _dumps(root: etree._Element) -> bytes:
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _child_key(el: etree._Element) -> str:
    """Identity for pPr/rPr children when diffing."""
    loc = _local(el.tag)
    # include distinguishing attrs
    bits = [loc]
    for a in ("val", "w", "type", "ascii", "hAnsi", "eastAsia", "cs"):
        q = f"{{{W}}}{a}"
        if q in el.attrib:
            bits.append(f"{a}={el.attrib[q]}")
    # spacing / indent carry multiple attrs — fingerprint whole attrib set
    if loc in {"spacing", "ind", "rFonts", "pgSz", "pgMar"}:
        bits.append("|".join(f"{_local(k)}={v}" for k, v in sorted(el.attrib.items())))
    return "/".join(bits)


def _pr_index(pr: etree._Element | None) -> dict[str, etree._Element]:
    if pr is None:
        return {}
    out: dict[str, etree._Element] = {}
    for ch in list(pr):
        out[_child_key(ch)] = ch
    return out


def _merge_pr(base: etree._Element | None, overlay: etree._Element | None) -> etree._Element | None:
    if base is None and overlay is None:
        return None
    tag = _qn("w:pPr") if (base is not None and _local(base.tag) == "pPr") or (
        overlay is not None and _local(overlay.tag) == "pPr"
    ) else _qn("w:rPr")
    # detect from either
    for src in (overlay, base):
        if src is not None:
            loc = _local(src.tag)
            if loc == "pPr":
                tag = _qn("w:pPr")
            elif loc == "rPr":
                tag = _qn("w:rPr")
            break
    merged = etree.Element(tag)
    idx = _pr_index(base)
    idx.update(_pr_index(overlay))  # overlay wins same key
    for el in idx.values():
        merged.append(copy.deepcopy(el))
    return merged


def _diff_pr(eff: etree._Element | None, base: etree._Element | None, *, pr_tag: str) -> etree._Element | None:
    """Children in eff that differ from base."""
    if eff is None:
        return None
    base_idx = _pr_index(base)
    delta = etree.Element(_qn(pr_tag))
    for key, el in _pr_index(eff).items():
        other = base_idx.get(key)
        if other is None:
            delta.append(copy.deepcopy(el))
            continue
        # same key — compare canonical XML
        if etree.tostring(el) != etree.tostring(other):
            delta.append(copy.deepcopy(el))
    return delta if len(delta) else None


def _index_styles(styles_root: etree._Element) -> dict[str, etree._Element]:
    out: dict[str, etree._Element] = {}
    for s in styles_root.findall(_qn("w:style")):
        sid = s.get(f"{{{W}}}styleId")
        if sid:
            out[sid] = s
    return out


def _style_based_on(style_el: etree._Element) -> str | None:
    based = style_el.find(_qn("w:basedOn"))
    if based is None:
        return None
    return based.get(f"{{{W}}}val")


def _effective_pr(
    style_id: str,
    styles: dict[str, etree._Element],
    *,
    which: str,  # pPr | rPr
    cache: dict[tuple[str, str], etree._Element | None],
) -> etree._Element | None:
    key = (style_id, which)
    if key in cache:
        return cache[key]
    el = styles.get(style_id)
    if el is None:
        cache[key] = None
        return None
    parent_id = _style_based_on(el)
    parent_pr = None
    if parent_id:
        parent_pr = _effective_pr(parent_id, styles, which=which, cache=cache)
    own = el.find(_qn(f"w:{which}"))
    merged = _merge_pr(parent_pr, own)
    cache[key] = merged
    return merged


def _p_style_id(p: etree._Element) -> str | None:
    p_pr = p.find(_qn("w:pPr"))
    if p_pr is None:
        return None
    st = p_pr.find(_qn("w:pStyle"))
    if st is None:
        return None
    return st.get(f"{{{W}}}val")


def _should_bake(style_id: str | None) -> bool:
    if not style_id:
        return False
    if style_id in _KEEP_STYLES:
        return False
    # custom Para* / Sec-adjacent paragraph roles etc.
    return True


def _ensure_ppr(p: etree._Element) -> etree._Element:
    p_pr = p.find(_qn("w:pPr"))
    if p_pr is None:
        p_pr = etree.Element(_qn("w:pPr"))
        p.insert(0, p_pr)
    return p_pr


def _set_p_style(p_pr: etree._Element, style_id: str) -> None:
    st = p_pr.find(_qn("w:pStyle"))
    if st is None:
        st = etree.Element(_qn("w:pStyle"))
        p_pr.insert(0, st)
    st.set(f"{{{W}}}val", style_id)


def _apply_delta_to_pr(host_pr: etree._Element, delta: etree._Element | None, *, skip_tags: set[str] | None = None) -> None:
    if delta is None:
        return
    skip_tags = skip_tags or set()
    existing = {_child_key(ch): ch for ch in list(host_pr) if _local(ch.tag) not in skip_tags}
    for ch in list(delta):
        if _local(ch.tag) in skip_tags:
            continue
        key = _child_key(ch)
        if key in existing:
            host_pr.remove(existing[key])
        host_pr.append(copy.deepcopy(ch))


def _strip_markers_in_paragraph(p: etree._Element) -> int:
    n = 0
    for t in p.iter(_qn("w:t")):
        if not t.text:
            continue
        new = _MARKER_RE.sub("", t.text)
        if new != t.text:
            n += 1
            t.text = new
    return n


def _bake_paragraphs_in_root(
    root: etree._Element,
    styles: dict[str, etree._Element],
    *,
    strip_markers: bool,
    cache: dict,
) -> dict[str, int]:
    stats = {"baked": 0, "skipped": 0, "markers_stripped": 0}
    normal_p = _effective_pr("Normal", styles, which="pPr", cache=cache)
    normal_r = _effective_pr("Normal", styles, which="rPr", cache=cache)

    for p in root.iter(_qn("w:p")):
        sid = _p_style_id(p)
        if not _should_bake(sid):
            stats["skipped"] += 1
            if strip_markers:
                stats["markers_stripped"] += _strip_markers_in_paragraph(p)
            continue
        assert sid is not None
        eff_p = _effective_pr(sid, styles, which="pPr", cache=cache)
        eff_r = _effective_pr(sid, styles, which="rPr", cache=cache)
        # merge existing direct pPr (except pStyle) into effective before diff
        p_pr = p.find(_qn("w:pPr"))
        direct_p = None
        sect = None
        if p_pr is not None:
            direct_p = etree.Element(_qn("w:pPr"))
            for ch in list(p_pr):
                loc = _local(ch.tag)
                if loc in {"pStyle"}:
                    continue
                if loc == "sectPr":
                    sect = copy.deepcopy(ch)
                    continue
                direct_p.append(copy.deepcopy(ch))
            if not len(direct_p):
                direct_p = None
        eff_p = _merge_pr(eff_p, direct_p)
        delta_p = _diff_pr(eff_p, normal_p, pr_tag="w:pPr")
        delta_r = _diff_pr(eff_r, normal_r, pr_tag="w:rPr")

        # rewrite pPr
        new_ppr = etree.Element(_qn("w:pPr"))
        st = etree.SubElement(new_ppr, _qn("w:pStyle"))
        st.set(f"{{{W}}}val", "Normal")
        if delta_p is not None:
            for ch in list(delta_p):
                new_ppr.append(ch)
        if sect is not None:
            new_ppr.append(sect)
        if p_pr is not None:
            p.remove(p_pr)
        p.insert(0, new_ppr)

        # apply char delta to runs that don't already override (merge)
        if delta_r is not None:
            for r in p.findall(_qn("w:r")):
                r_pr = r.find(_qn("w:rPr"))
                if r_pr is None:
                    r_pr = etree.Element(_qn("w:rPr"))
                    r.insert(0, r_pr)
                _apply_delta_to_pr(r_pr, delta_r)
            for link in p.findall(_qn("w:hyperlink")):
                for r in link.findall(_qn("w:r")):
                    r_pr = r.find(_qn("w:rPr"))
                    if r_pr is None:
                        r_pr = etree.Element(_qn("w:rPr"))
                        r.insert(0, r_pr)
                    _apply_delta_to_pr(r_pr, delta_r)

        stats["baked"] += 1
        if strip_markers:
            stats["markers_stripped"] += _strip_markers_in_paragraph(p)
    return stats


def bake_docx_to_direct(
    src: Path,
    dst: Path,
    *,
    strip_markers: bool = True,
) -> dict[str, Any]:
    """Copy src→dst and bake custom paragraph styles to Normal + direct deltas."""
    src = src.resolve()
    dst = dst.resolve()
    if src != dst:
        shutil.copy2(src, dst)
    parts = _read_parts(dst)
    styles_xml = parts.get("word/styles.xml")
    if not styles_xml:
        raise FileNotFoundError(f"no word/styles.xml in {dst}")
    styles_root = _parse(styles_xml)
    styles = _index_styles(styles_root)
    cache: dict[tuple[str, str], etree._Element | None] = {}

    totals = {"baked": 0, "skipped": 0, "markers_stripped": 0, "parts": []}
    for name, data in list(parts.items()):
        low = name.lower()
        if not (low.startswith("word/") and low.endswith(".xml")):
            continue
        if "document.xml" not in low and "header" not in low and "footer" not in low:
            continue
        try:
            root = _parse(data)
        except etree.XMLSyntaxError:
            continue
        if root.find(".//" + _qn("w:p")) is None and _local(root.tag) != "p":
            # still try iter
            pass
        st = _bake_paragraphs_in_root(root, styles, strip_markers=strip_markers, cache=cache)
        if st["baked"] or st["markers_stripped"]:
            parts[name] = _dumps(root)
            totals["baked"] += st["baked"]
            totals["skipped"] += st["skipped"]
            totals["markers_stripped"] += st["markers_stripped"]
            totals["parts"].append({"part": name, **st})

    _write_parts(dst, parts)
    return totals


def reapply_paragraph_props_via_map(
    dst: Path,
    role_params: dict,
    map_json: dict,
    *,
    styles_json: dict | None = None,
) -> dict[str, Any]:
    """Optional reinforcement: set Normal + role props on mapped paragraph indices via officecli.

    Used when OOXML bake is insufficient or as a second pass from format params.
    Batches ``set`` when ``OFFICECLI_BATCH`` is enabled.
    """
    from LongDocFmtEval.common.officecli_runner import (  # noqa: PLC0415
        officecli_batch_session,
        set_props,
    )
    from LongDocFmtEval.pipeline_auto.seed.seed_ir import style_props_for_paragraph  # noqa: PLC0415

    dst = Path(dst).resolve()
    # para_index -> role_id
    idx_role: dict[int, str] = {}
    for rid, locs in (map_json.get("paragraph") or {}).items():
        if isinstance(locs, list):
            for i in locs:
                try:
                    idx_role[int(i)] = str(rid)
                except (TypeError, ValueError):
                    continue
        elif isinstance(locs, dict) and locs.get("para_index") is not None:
            try:
                idx_role[int(locs["para_index"])] = str(rid)
            except (TypeError, ValueError):
                continue
    # styles.json exemplars
    for rid, loc in ((styles_json or {}).get("paragraph") or {}).items():
        if isinstance(loc, dict) and loc.get("para_index") is not None:
            try:
                idx_role.setdefault(int(loc["para_index"]), str(rid))
            except (TypeError, ValueError):
                continue

    applied = []
    errors = []
    ocli_mode = "sequential"
    ocli_cmds = 0
    ocli_batches = 0
    with officecli_batch_session(dst) as sess:
        for idx, rid in sorted(idx_role.items()):
            spec = role_params.get(rid) or {}
            if str(spec.get("object") or "") not in (
                "paragraph", "paragraph.body", "paragraph.table_cell", "paragraph.cell_text", "",
            ):
                if not str(rid).startswith("Para"):
                    continue
            # Para* including table-paragraph still become Word paragraph styles
            props = style_props_for_paragraph(spec.get("props") or {})
            payload = {"style": "Normal", **props}
            try:
                set_props(dst, f"/body/p[{idx}]", payload)
                applied.append({"para_index": idx, "role_id": rid})
            except Exception as exc:  # noqa: BLE001
                errors.append({"para_index": idx, "role_id": rid, "error": str(exc)[:200]})
        ocli_mode = sess.mode
        ocli_cmds = int(sess.n_enqueued)
        ocli_batches = int(sess.n_batches)
    return {
        "applied": applied,
        "errors": errors,
        "mapped": len(idx_role),
        "officecli_mode": ocli_mode,
        "officecli_cmds": ocli_cmds,
        "officecli_batches": ocli_batches,
    }
