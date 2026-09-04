from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable

import yaml

from LongDocFormatter.workflow.contracts import Layer

_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_WHITELIST = _PACKAGE_ROOT / "config" / "officecli_whitelist.yaml"
_DEFAULT_WHITELIST_EN = _PACKAGE_ROOT / "config" / "officecli_whitelist_en.yaml"

LAYER_TO_WHITELIST_SECTION: Dict[str, str] = {
    "section": "section",
    "paragraph.body": "paragraph",
    "paragraph.table_cell": "paragraph",
    "table": "table_format",
    "image": "image",
    "run": "run",
}

_SKIP_READBACK = {
    "path", "type", "text", "childCount", "children", "id", "name", "customStyle",
    "basedOn", "basedOn.path", "next", "linked", "uiPriority", "qFormat", "semiHidden",
    "unhideWhenUsed", "personal", "personalCompose", "personalReply", "aliases",
    "paraId", "style", "styleId", "styleName", "lineRule",
    "headerRef", "headerRef.default", "headerRef.first", "headerRef.even",
    "footerRef", "footerRef.default", "footerRef.first", "footerRef.even",
    "sectionType", "relId", "contentType", "fileSize", "alt",
}

_EFFECTIVE_TO_PROP = {
    "effective.size": "size",
    "effective.bold": "bold",
    "effective.italic": "italic",
    "effective.alignment": "align",
    "effective.spaceBefore": "spaceBefore",
    "effective.spaceAfter": "spaceAfter",
    "effective.lineSpacing": "lineSpacing",
    "effective.firstLineIndent": "firstLineIndent",
    "effective.hangingIndent": "hangingIndent",
    "effective.leftIndent": "indent",
    "effective.rightIndent": "rightIndent",
    "effective.outlineLvl": "outlineLvl",
    "effective.color": "color",
    "effective.underline": "underline",
    "effective.keepNext": "keepNext",
    "effective.pageBreakBefore": "pageBreakBefore",
}

# List chrome is instance-level. indent is rejected by some officecli style adds.
# keepNext / pageBreakBefore / outlineLvl belong on the style (schema add+get).
PARAGRAPH_STYLE_SKIP = {
    "listStyle", "numId", "numLevel", "indent",
}

_DOC_DEFAULT_SRC = "/docdefaults"

HEADER_FOOTER_KEYS = {
    "text", "field", "align", "size", "color", "bold", "italic", "type", "direction", "font",
}

CELL_KEYS = {
    "valign", "fill", "shading",
    "border.all", "border.top", "border.bottom", "border.left", "border.right",
}

TABLE_FORMAT_KEYS = {
    "align", "width", "layout", "repeat_header",
    "border.all", "border.top", "border.bottom", "border.left", "border.right",
}


def load_whitelist(*, locale: str = "en") -> Dict[str, Any]:
    path = _DEFAULT_WHITELIST_EN if str(locale).lower().startswith("en") else _DEFAULT_WHITELIST
    if not path.is_file():
        path = _DEFAULT_WHITELIST
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def whitelist_keys(layer: Layer | str, whitelist: Dict[str, Any] | None = None) -> list[str]:
    wl = whitelist or load_whitelist()
    section = LAYER_TO_WHITELIST_SECTION.get(str(layer), str(layer))
    block = wl.get(section) or {}
    return [str(k) for k in block.keys()]


def whitelist_forms(layer: Layer | str, whitelist: Dict[str, Any] | None = None) -> Dict[str, str]:
    wl = whitelist or load_whitelist()
    section = LAYER_TO_WHITELIST_SECTION.get(str(layer), str(layer))
    block = wl.get(section) or {}
    out: Dict[str, str] = {}
    for k, meta in block.items():
        if isinstance(meta, dict):
            out[str(k)] = str(meta.get("form") or "")
        else:
            out[str(k)] = str(meta)
    return out


def filter_props(props: Dict[str, Any] | None, keys: Iterable[str]) -> Dict[str, Any]:
    allowed = set(keys)
    out: Dict[str, Any] = {}
    for k, v in (props or {}).items():
        if k in allowed and v not in (None, "", "none"):
            out[k] = v
    return out


def clean_readback(fmt: Dict[str, Any] | None) -> Dict[str, Any]:
    fmt = fmt or {}
    out: Dict[str, Any] = {}
    for k, v in fmt.items():
        if k in _SKIP_READBACK or v in (None, "", "none"):
            continue
        if k.endswith(".path") or k.endswith(".xml") or str(k).startswith("markRPr"):
            continue
        if str(k).startswith("effective."):
            continue
        if str(k).startswith("headerRef") or str(k).startswith("footerRef"):
            continue
        out[k] = v
    if "font.latin" not in out:
        latin = out.get("font.ascii") or out.get("font.hAnsi")
        if latin:
            out["font.latin"] = latin
    ea = out.get("font.eastAsia") or out.get("font.ea")
    if ea:
        out["font.ea"] = ea
    return out


def _src_is_doc_defaults(fmt: Dict[str, Any], key: str) -> bool:
    src = str(fmt.get(f"{key}.src") or "").strip().lower()
    return src == _DOC_DEFAULT_SRC


def props_from_effective(
    fmt: Dict[str, Any] | None,
    *,
    skip_doc_defaults: bool = False,
) -> Dict[str, Any]:
    fmt = fmt or {}
    out: Dict[str, Any] = {}
    for ek, pk in _EFFECTIVE_TO_PROP.items():
        v = fmt.get(ek)
        if v in (None, "", "none"):
            continue
        if skip_doc_defaults and _src_is_doc_defaults(fmt, ek):
            continue
        out[pk] = v
    ea = fmt.get("effective.font.eastAsia") or fmt.get("effective.font.ea")
    if ea and not (
        skip_doc_defaults
        and (
            _src_is_doc_defaults(fmt, "effective.font.eastAsia")
            or _src_is_doc_defaults(fmt, "effective.font.ea")
        )
    ):
        out["font.ea"] = ea
    latin = fmt.get("effective.font.ascii") or fmt.get("effective.font.hAnsi")
    if latin and not (
        skip_doc_defaults
        and (
            _src_is_doc_defaults(fmt, "effective.font.ascii")
            or _src_is_doc_defaults(fmt, "effective.font.hAnsi")
        )
    ):
        out["font.latin"] = latin
    return out


def merge_element_props(fmt: Dict[str, Any] | None) -> Dict[str, Any]:
    """Direct format wins; fill gaps from effective.*."""
    direct = clean_readback(fmt)
    effective = props_from_effective(fmt)
    merged = dict(effective)
    merged.update(direct)
    return merged


def merge_paragraph_props(
    para_fmt: Dict[str, Any] | None,
    style_fmt: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Match AutoDataBuild extract: ``/styles/{id}`` first, then paragraph effective.

    Paragraph ``effective.*`` values whose ``.src`` is ``/docDefaults`` are not
    role props. Direct paragraph overrides still win.
    """
    merged = merge_element_props(style_fmt)
    para_direct = clean_readback(para_fmt)
    para_eff = props_from_effective(para_fmt, skip_doc_defaults=True)
    for k, v in para_eff.items():
        if k not in merged:
            merged[k] = v
    merged.update(para_direct)
    return merged


def style_props_for_paragraph(props: Dict[str, Any] | None) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in (props or {}).items():
        if v in (None, "", "none") or k in PARAGRAPH_STYLE_SKIP:
            continue
        out[k] = v
    return out


def dump_whitelist_section(section: str, *, locale: str = "en") -> str:
    wl = load_whitelist(locale=locale)
    block = wl.get(section) or {}
    lines = []
    for k, meta in block.items():
        form = meta.get("form") if isinstance(meta, dict) else meta
        lines.append(f"- `{k}`: {form}")
    return "\n".join(lines)


def dump_whitelist_for_prompt(layer: Layer | str, *, locale: str = "en") -> str:
    if str(layer) == "table":
        tf = dump_whitelist_section("table_format", locale=locale)
        cells = dump_whitelist_section("cell_format", locale=locale)
        return (
            "table_format (whole table only):\n"
            f"{tf}\n"
            "cells named slots (header/data/label/value/stub/note — not independent style_ids):\n"
            f"{cells}"
        )
    if str(layer) == "section":
        sec = dump_whitelist_section("section", locale=locale)
        hf = "\n".join(
            f"- `{k}`: "
            + {
                "text": 'header/footer copy; "" for none (do not invent)',
                "field": '"page" for a page-number field (preferred for body footers)',
                "align": "left|center|right",
                "size": '"9pt"|"10pt"|"10.5pt"',
                "color": "#RRGGBB",
                "bold": "true|false",
                "italic": "true|false",
                "type": '"first" on header_first only',
                "direction": "ltr|rtl",
                "font": "family name (not font.ea / font.latin)",
            }[k]
            for k in ("text", "field", "align", "size", "color", "bold", "italic", "type", "font")
        )
        return (
            "section page setup (props):\n"
            f"{sec}\n"
            "header / footer / header_first (nested objects, NOT inside props):\n"
            f"{hf}"
        )
    forms = whitelist_forms(layer, load_whitelist(locale=locale))
    lines = [f"- `{k}`: {form}" for k, form in forms.items()]
    return "\n".join(lines)
