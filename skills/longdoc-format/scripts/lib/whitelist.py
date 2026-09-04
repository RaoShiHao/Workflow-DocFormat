"""Allowed officecli --prop keys. Bundled here so the skill does not import the repo."""

from __future__ import annotations

from typing import Any, Iterable

LAYERS = (
    "section",
    "paragraph.body",
    "paragraph.table_cell",
    "table",
    "image",
    "run",
)

PARAGRAPH_KEYS = (
    "font.latin",
    "font.ea",
    "size",
    "bold",
    "italic",
    "color",
    "align",
    "lineSpacing",
    "spaceBefore",
    "spaceAfter",
    "firstLineIndent",
    "hangingIndent",
    "indent",
    "rightIndent",
    "outlineLvl",
    "keepNext",
    "pageBreakBefore",
    "listStyle",
    "numLevel",
)

RUN_KEYS = (
    "bold",
    "italic",
    "superscript",
    "subscript",
    "caps",
    "smallcaps",
    "color",
    "underline",
    "font.ea",
    "font.latin",
    "size",
)

SECTION_KEYS = (
    "marginTop",
    "marginBottom",
    "marginLeft",
    "marginRight",
    "marginHeader",
    "marginFooter",
    "orientation",
    "columns",
    "pgBorders",
    "type",
    "pageNumFmt",
    "pageStart",
    "titlePage",
)

TABLE_FORMAT_KEYS = (
    "border.all",
    "border.top",
    "border.bottom",
    "border.left",
    "border.right",
    "align",
    "width",
    "layout",
    "repeat_header",
)

CELL_KEYS = (
    "border.all",
    "border.top",
    "border.bottom",
    "border.left",
    "border.right",
    "fill",
    "valign",
    "shading",
)

# List chrome stays on the paragraph instance, not the named style.
PARAGRAPH_STYLE_SKIP = frozenset({"listStyle", "numId", "numLevel", "indent"})
INSTANCE_PARA_KEYS = frozenset({
    "listStyle", "numId", "numLevel", "indent",
    "keepNext", "pageBreakBefore", "outlineLvl",
})

IMAGE_KEYS = ("width", "height", "hAlign")

HEADER_FOOTER_KEYS = (
    "text",
    "field",
    "align",
    "size",
    "color",
    "bold",
    "italic",
    "type",
    "direction",
    "font",
    "font.ea",
    "font.latin",
)

_SKIP = {
    "path",
    "type",
    "text",
    "childCount",
    "children",
    "id",
    "name",
    "paraId",
    "style",
    "styleId",
    "styleName",
    "lineRule",
}

_LAYER_KEYS = {
    "section": SECTION_KEYS,
    "paragraph.body": PARAGRAPH_KEYS,
    "paragraph.table_cell": PARAGRAPH_KEYS,
    "table": TABLE_FORMAT_KEYS,
    "image": IMAGE_KEYS,
    "run": RUN_KEYS,
}


def keys_for(layer: str) -> tuple[str, ...]:
    return _LAYER_KEYS.get(str(layer), ())


def whitelist_keys(layer: str, whitelist: dict[str, Any] | None = None) -> list[str]:
    del whitelist
    return list(keys_for(layer))


def filter_props(props: dict[str, Any] | None, allowed: Iterable[str]) -> dict[str, Any]:
    allow = set(allowed)
    out: dict[str, Any] = {}
    for k, v in (props or {}).items():
        if k in allow and v not in (None, "", "none"):
            out[k] = v
    return out


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

_DOC_DEFAULT_SRC = "/docdefaults"


def clean_readback(fmt: dict[str, Any] | None) -> dict[str, Any]:
    fmt = fmt or {}
    out: dict[str, Any] = {}
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


def _src_is_doc_defaults(fmt: dict[str, Any], key: str) -> bool:
    src = str(fmt.get(f"{key}.src") or "").strip().lower()
    return src == _DOC_DEFAULT_SRC


def props_from_effective(
    fmt: dict[str, Any] | None,
    *,
    skip_doc_defaults: bool = False,
) -> dict[str, Any]:
    fmt = fmt or {}
    out: dict[str, Any] = {}
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


def merge_element_props(fmt: dict[str, Any] | None) -> dict[str, Any]:
    """Direct format wins; fill gaps from effective.*."""
    direct = clean_readback(fmt)
    effective = props_from_effective(fmt)
    merged = dict(effective)
    merged.update(direct)
    return merged


def merge_paragraph_props(
    para_fmt: dict[str, Any] | None,
    style_fmt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Style first, then paragraph effective (skip /docDefaults), then direct overrides."""
    merged = merge_element_props(style_fmt)
    para_direct = clean_readback(para_fmt)
    para_eff = props_from_effective(para_fmt, skip_doc_defaults=True)
    for k, v in para_eff.items():
        if k not in merged:
            merged[k] = v
    merged.update(para_direct)
    return merged


def clean_format(fmt: dict[str, Any] | None) -> dict[str, Any]:
    fmt = dict(fmt or {})
    out: dict[str, Any] = {}
    for k, v in fmt.items():
        if k in _SKIP or v in (None, "", "none"):
            continue
        if str(k).startswith("effective.") or str(k).endswith(".path") or str(k).endswith(".src"):
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
    for ek, pk in (
        ("effective.size", "size"),
        ("effective.bold", "bold"),
        ("effective.italic", "italic"),
        ("effective.alignment", "align"),
        ("effective.spaceBefore", "spaceBefore"),
        ("effective.spaceAfter", "spaceAfter"),
        ("effective.lineSpacing", "lineSpacing"),
        ("effective.firstLineIndent", "firstLineIndent"),
        ("effective.outlineLvl", "outlineLvl"),
        ("effective.color", "color"),
    ):
        if pk not in out and fmt.get(ek) not in (None, "", "none"):
            out[pk] = fmt[ek]
    return out
