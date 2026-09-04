"""Resolve Word outlineLvl from paragraph pPr, else the linked style (basedOn chain).

officecli paragraph get/query typically returns ``outlineLvl: null`` because the
value lives on the style definition (``styles.xml``), not on the instance ``pPr``.
Template extract must read OOXML or the copied custom styles lose Navigation/TOC
levels.
"""
from __future__ import annotations

import re
import zipfile
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple
from xml.etree import ElementTree as ET

from LongDocFormatter.officecli.read._ooxml_section import para_id_from_path

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_W14_NS = "http://schemas.microsoft.com/office/word/2010/wordml"
_NS = {"w": _W_NS}
_W_VAL = f"{{{_W_NS}}}val"
_PARA_ID_ATTR = f"{{{_W14_NS}}}paraId"

_HEADING_ID = re.compile(r"^(?:heading|标题)\s*([1-9])$", re.I)
_HEADING_ID_COMPACT = re.compile(r"^(?:heading|标题)([1-9])$", re.I)


def coerce_outline_lvl(value: object) -> int | None:
    if value in (None, "", "none"):
        return None
    try:
        lvl = int(value)
    except (TypeError, ValueError):
        return None
    if 0 <= lvl <= 9:
        return lvl
    return None


def _outline_from_p_pr(p_pr: ET.Element | None) -> int | None:
    if p_pr is None:
        return None
    ol = p_pr.find("w:outlineLvl", _NS)
    if ol is None:
        return None
    return coerce_outline_lvl(ol.get(_W_VAL))


def _bool_from_p_pr(p_pr: ET.Element | None, tag: str) -> bool | None:
    if p_pr is None:
        return None
    el = p_pr.find(f"w:{tag}", _NS)
    if el is None:
        return None
    val = el.get(_W_VAL)
    if val is None:
        return True
    return str(val).strip().lower() not in {"0", "false", "off"}


def _ppr_inherited_props(p_pr: ET.Element | None) -> dict[str, object]:
    out: dict[str, object] = {}
    ol = _outline_from_p_pr(p_pr)
    if ol is not None:
        out["outlineLvl"] = ol
    keep = _bool_from_p_pr(p_pr, "keepNext")
    if keep is not None:
        out["keepNext"] = keep
    brk = _bool_from_p_pr(p_pr, "pageBreakBefore")
    if brk is not None:
        out["pageBreakBefore"] = brk
    return out


def _builtin_heading_level(style_id: str | None, style_name: str | None) -> int | None:
    for raw in (style_id, style_name):
        if not raw:
            continue
        text = str(raw).strip()
        match = _HEADING_ID.match(text) or _HEADING_ID_COMPACT.match(text)
        if match:
            return int(match.group(1)) - 1
    return None


def _style_name(style_el: ET.Element) -> str | None:
    name_el = style_el.find("w:name", _NS)
    if name_el is None:
        return None
    return name_el.get(_W_VAL)


def _build_style_outline_index(styles_xml: bytes) -> dict[str, int]:
    root = ET.fromstring(styles_xml)
    by_id: dict[str, ET.Element] = {}
    for style in root.findall("w:style", _NS):
        if style.get(f"{{{_W_NS}}}type") != "paragraph":
            continue
        sid = style.get(f"{{{_W_NS}}}styleId")
        if sid:
            by_id[sid] = style

    cache: dict[str, int | None] = {}

    def resolve(sid: str | None, seen: set[str]) -> int | None:
        if not sid:
            return None
        if sid in cache:
            return cache[sid]
        if sid in seen:
            return None
        seen.add(sid)
        style_el = by_id.get(sid)
        if style_el is None:
            lvl = _builtin_heading_level(sid, None)
            cache[sid] = lvl
            return lvl
        lvl = _outline_from_p_pr(style_el.find("w:pPr", _NS))
        if lvl is None:
            lvl = _builtin_heading_level(sid, _style_name(style_el))
        if lvl is None:
            based = style_el.find("w:basedOn", _NS)
            parent = based.get(_W_VAL) if based is not None else None
            lvl = resolve(parent, seen)
        cache[sid] = lvl
        return lvl

    out: dict[str, int] = {}
    for sid in by_id:
        lvl = resolve(sid, set())
        if lvl is not None:
            out[sid] = lvl
    return out


class BodyOutlineIndex(NamedTuple):
    by_para_id: dict[str, int]
    by_index: dict[int, int]
    props_by_para_id: dict[str, dict[str, object]] = {}
    props_by_index: dict[int, dict[str, object]] = {}

    def lookup(self, path: str, *, location_id: object = None) -> int | None:
        props = self.lookup_props(path, location_id=location_id)
        return coerce_outline_lvl(props.get("outlineLvl")) if props else None

    def lookup_props(self, path: str, *, location_id: object = None) -> dict[str, object]:
        pid = para_id_from_path(path)
        if pid:
            found = self.props_by_para_id.get(pid) or self.props_by_para_id.get(pid.upper())
            if found:
                return dict(found)
            lvl = self.by_para_id.get(pid) or self.by_para_id.get(pid.upper())
            if lvl is not None:
                return {"outlineLvl": lvl}
        if location_id is None:
            return {}
        try:
            idx = int(location_id)
        except (TypeError, ValueError):
            return {}
        found = self.props_by_index.get(idx)
        if found:
            return dict(found)
        lvl = self.by_index.get(idx)
        if lvl is not None:
            return {"outlineLvl": lvl}
        return {}


@lru_cache(maxsize=8)
def load_body_outline_index(doc_path: str) -> BodyOutlineIndex:
    path = Path(doc_path)
    with zipfile.ZipFile(path) as archive:
        styles_xml = archive.read("word/styles.xml")
        document = ET.fromstring(archive.read("word/document.xml"))

    style_lvl = _build_style_outline_index(styles_xml)
    style_root = ET.fromstring(styles_xml)
    style_props: dict[str, dict[str, object]] = {}
    for style in style_root.findall("w:style", _NS):
        if style.get(f"{{{_W_NS}}}type") != "paragraph":
            continue
        sid = style.get(f"{{{_W_NS}}}styleId")
        if not sid:
            continue
        bag = _ppr_inherited_props(style.find("w:pPr", _NS))
        if sid in style_lvl and "outlineLvl" not in bag:
            bag["outlineLvl"] = style_lvl[sid]
        if bag:
            style_props[sid] = bag
    body = document.find("w:body", _NS)
    by_para_id: dict[str, int] = {}
    by_index: dict[int, int] = {}
    props_by_para_id: dict[str, dict[str, object]] = {}
    props_by_index: dict[int, dict[str, object]] = {}
    if body is None:
        return BodyOutlineIndex(by_para_id, by_index, props_by_para_id, props_by_index)

    index = 0
    for child in body:
        tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if tag != "p":
            continue
        index += 1
        p_pr = child.find("w:pPr", _NS)
        sid = None
        if p_pr is not None:
            p_style = p_pr.find("w:pStyle", _NS)
            if p_style is not None:
                sid = p_style.get(_W_VAL)
        bag: dict[str, object] = {}
        if sid and sid in style_props:
            bag.update(style_props[sid])
        bag.update(_ppr_inherited_props(p_pr))
        lvl = coerce_outline_lvl(bag.get("outlineLvl"))
        if lvl is None:
            lvl = style_lvl.get(sid or "")
        if lvl is None:
            lvl = _builtin_heading_level(sid, None)
        if lvl is not None:
            bag["outlineLvl"] = lvl
            by_index[index] = lvl
            para_id = child.get(_PARA_ID_ATTR)
            if para_id:
                by_para_id[para_id] = lvl
                by_para_id[para_id.upper()] = lvl
        if not bag:
            continue
        props_by_index[index] = bag
        para_id = child.get(_PARA_ID_ATTR)
        if para_id:
            props_by_para_id[para_id] = bag
            props_by_para_id[para_id.upper()] = bag
    return BodyOutlineIndex(by_para_id, by_index, props_by_para_id, props_by_index)


def body_outline_index(doc: str | Path) -> BodyOutlineIndex:
    return load_body_outline_index(str(Path(doc).resolve()))
