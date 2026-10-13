"""Shared IR helpers for pipeline_role/seed."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

PARA_MARKER_RE = re.compile(r"^\{([A-Za-z][A-Za-z0-9_]*)\}")


def para_marker(role_id: str) -> str:
    """Visible body prefix, e.g. {ParaTocEntry}."""
    return "{" + str(role_id) + "}"


def with_para_marker(role_id: str, text: str) -> str:
    """Ensure paragraph body starts with {role_id}; do not double-prefix."""
    text = str(text or "")
    m = PARA_MARKER_RE.match(text)
    if m and m.group(1) == role_id:
        return text
    if m:
        # replace wrong/old marker
        text = text[m.end() :]
    return para_marker(role_id) + text


def strip_para_marker(text: str) -> tuple[str | None, str]:
    """Return (role_id|None, body_without_marker)."""
    text = str(text or "")
    m = PARA_MARKER_RE.match(text)
    if not m:
        return None, text
    return m.group(1), text[m.end() :]


def shift_run_spans_for_marker(
    runs: list[dict[str, Any]] | None,
    role_id: str,
    original_text: str,
) -> list[dict[str, Any]]:
    """Shift start/end when prefixing {role_id} onto original_text."""
    original_text = str(original_text or "")
    marked = with_para_marker(role_id, original_text)
    # if already marked, spans assumed relative to marked text
    if PARA_MARKER_RE.match(original_text):
        return [dict(r) for r in (runs or [])]
    delta = len(para_marker(role_id))
    out = []
    for r in runs or []:
        nr = dict(r)
        nr["start"] = int(r.get("start", 0)) + delta
        nr["end"] = int(r.get("end", 0)) + delta
        out.append(nr)
    # sanity vs marked length
    n = len(marked)
    for nr in out:
        nr["start"] = max(0, min(nr["start"], n))
        nr["end"] = max(nr["start"], min(nr["end"], n))
    return out


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def dump_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# Executable format lives only in role_params.json — not copied into template.json.
ROLE_FORMAT_KEYS = (
    "props", "header", "footer", "header_first", "cells", "table_format",
)


def as_role_params_map(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict) or not data:
        return {}
    if isinstance(data.get("roles"), dict) and not any(
        k in ROLE_FORMAT_KEYS for k in data
    ):
        inner = data["roles"]
        if isinstance(inner, dict):
            return inner
    return data


def load_role_params_map(work_dir: Path) -> dict[str, Any]:
    """Load executable T* from ``role_params.json`` (legacy: ``template.json`` roles)."""
    from LongDocFmtEval.pipeline_auto.auto_paths import resolve_work_file  # noqa: PLC0415

    rp_path = resolve_work_file(Path(work_dir), "role_params.json")
    if rp_path.is_file():
        try:
            data = as_role_params_map(load_json(rp_path))
        except Exception:  # noqa: BLE001
            data = {}
        if data:
            return data
    tpl_path = resolve_work_file(Path(work_dir), "template.json")
    if tpl_path.is_file():
        try:
            roles = (load_json(tpl_path) or {}).get("roles") or {}
        except Exception:  # noqa: BLE001
            roles = {}
        if isinstance(roles, dict):
            return roles
    return {}


def merge_catalog_with_role_params(
    catalog: dict[str, Any] | None,
    role_params: dict[str, Any] | None,
) -> dict[str, Any]:
    """Catalog (names) + role_params (format). Format keys always come from role_params when present."""
    cat = catalog if isinstance(catalog, dict) else {}
    params = role_params if isinstance(role_params, dict) else {}
    out: dict[str, Any] = {}
    have_params = bool(params)
    for rid in set(cat) | set(params):
        base = dict(cat[rid]) if isinstance(cat.get(rid), dict) else {}
        rp = params.get(rid) if isinstance(params.get(rid), dict) else {}
        if have_params:
            for k in ROLE_FORMAT_KEYS:
                base.pop(k, None)
        if rp:
            base.update(rp)
        out[rid] = base
    return out


def load_merged_roles(work_dir: Path, template: dict[str, Any] | None = None) -> dict[str, Any]:
    """Roles for render/write: names from ``template.json``, props from ``role_params.json``."""
    if template is None:
        from LongDocFmtEval.pipeline_auto.auto_paths import resolve_work_file  # noqa: PLC0415

        tpl_path = resolve_work_file(Path(work_dir), "template.json")
        try:
            template = load_json(tpl_path) if tpl_path.is_file() else {}
        except Exception:  # noqa: BLE001
            template = {}
    catalog = (template or {}).get("roles") if isinstance(template, dict) else {}
    return merge_catalog_with_role_params(
        catalog if isinstance(catalog, dict) else {},
        load_role_params_map(work_dir),
    )


def role_kind(role_id: str) -> str:
    if role_id.startswith("Sec"):
        return "section"
    if role_id.startswith("Tbl"):
        return "table"
    if role_id.startswith("Img"):
        return "image"
    if role_id.startswith("Run"):
        return "run"
    if role_id.startswith("ParaTbl"):
        return "table_cell"
    if role_id.startswith("Para"):
        return "paragraph"
    return "paragraph"


def _normalize_len_for_officecli(val: Any) -> Any:
    """officecli style/paragraph lengths reject ``em``; map common LLM values to pt/cm."""
    if not isinstance(val, str):
        return val
    s = val.strip()
    m = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*em", s, flags=re.I)
    if not m:
        return val
    # 中文排版习惯：1em≈当前字号宽度；白名单「两字符首行」≈24pt
    n = float(m.group(1))
    pt = int(round(n * 12))  # assume ~12pt body → 2em≈24pt
    if abs(n - 2.0) < 1e-6:
        pt = 24
    return f"{pt}pt"


_MARGIN_LEN_KEYS = (
    "marginTop", "marginBottom", "marginLeft", "marginRight",
    "marginHeader", "marginFooter",
)


def _normalize_margin_value(val: Any) -> Any:
    """Bare number → ``cm`` (officecli treats unitless margins as ~0)."""
    if val is None or isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        n = float(val)
        s = str(int(n)) if n == int(n) else str(n)
        return f"{s}cm"
    s = str(val).strip()
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return f"{s}cm"
    return val


# format_focus / schema snake_case (role library) → officecli --prop keys.
# LLM often copies the focus tag into props; those names are not writable.
PROP_KEY_ALIASES: dict[str, str] = {
    "space_before": "spaceBefore",
    "space_after": "spaceAfter",
    "line_spacing": "lineSpacing",
    "first_line_indent": "firstLineIndent",
    "hanging_indent": "hangingIndent",
    "left_indent": "indent",
    "right_indent": "rightIndent",
    "outline_level": "outlineLvl",
    "alignment": "align",
    "font_size": "size",
    "keep_with_next": "keepNext",
    "page_break_before": "pageBreakBefore",
    "run_bold": "bold",
    "run_italic": "italic",
    "run_superscript": "superscript",
    "run_subscript": "subscript",
    "run_caps": "caps",
    "run_small_caps": "smallcaps",
    "run_color": "color",
    "run_underline": "underline",
    "run_font_ea": "font.ea",
    "run_font_latin": "font.latin",
    "header_repeat": "repeat_header",
    "header_distance": "marginHeader",
    "footer_distance": "marginFooter",
    "page_border": "pgBorders",
    "section_break_nextPage": "type",
    "header_footer_independent": "titlePage",
    "table_border_all": "border.all",
    "table_border_top": "border.top",
    "table_border_bottom": "border.bottom",
    "table_border_left": "border.left",
    "table_border_right": "border.right",
    "table_width_percent": "width",
    "table_width_autofit": "layout",
    "cell_border_all": "border.all",
    "cell_border_top": "border.top",
    "cell_border_bottom": "border.bottom",
    "cell_border_left": "border.left",
    "cell_border_right": "border.right",
    "cell_shading": "fill",
}

# Focus tag used as a flag key → (officecli key, value when truthy).
PROP_FLAG_ALIASES: dict[str, tuple[str, str]] = {
    "list_ordered": ("listStyle", "ordered"),
    "list_unordered": ("listStyle", "bullet"),
    "cell_valign_top": ("valign", "top"),
    "cell_valign_center": ("valign", "center"),
    "cell_valign_bottom": ("valign", "bottom"),
    "cell_align_h_left": ("align", "left"),
    "cell_align_h_center": ("align", "center"),
    "cell_align_h_right": ("align", "right"),
    "table_align_center": ("align", "center"),
    "table_align_left": ("align", "left"),
    "orientation_portrait": ("orientation", "portrait"),
    "orientation_landscape": ("orientation", "landscape"),
    "columns_one": ("columns", "1"),
    # Multi-column deprecated: treat as single column
    "columns_two": ("columns", "1"),
    "table_border_none": ("border.all", "none"),
    "cell_border_none": ("border.all", "none"),
}

_OFFICECLI_KEYS: frozenset[str] = frozenset({
    "font.latin", "font.ea", "font", "size", "bold", "italic", "color", "align",
    "lineSpacing", "spaceBefore", "spaceAfter", "firstLineIndent", "hangingIndent",
    "indent", "rightIndent", "outlineLvl", "keepNext", "pageBreakBefore",
    "listStyle", "numLevel", "numId", "superscript", "subscript", "caps",
    "smallcaps", "underline",
    "marginTop", "marginBottom", "marginLeft", "marginRight", "marginHeader",
    "marginFooter", "orientation", "columns", "columnSpace", "pgBorders", "type",
    "pageNumFmt", "pageStart", "titlePage",
    "border.all", "border.top", "border.bottom", "border.left", "border.right",
    "width", "layout", "repeat_header", "fill", "valign", "hAlign", "height",
})

_SIZE_CN: dict[str, str] = {
    "二号": "22pt", "小二": "18pt", "三号": "16pt", "小三": "15pt",
    "四号": "14pt", "小四": "12pt", "五号": "10.5pt", "小五": "9pt",
}

_LINE_SPACING_VAL: dict[str, str] = {
    "单倍": "1x", "single": "1x", "1": "1x", "1.0": "1x",
    "1.15": "1.15x",
    "1.5倍": "1.5x", "1.5": "1.5x", "one-and-a-half": "1.5x",
    "双倍": "2x", "double": "2x", "2": "2x", "2.0": "2x",
}

_ALIGN_VAL: dict[str, str] = {
    "居中": "center", "centre": "center", "中间": "center",
    "两端": "justify", "两端对齐": "justify", "justified": "justify", "both": "justify",
    "左": "left", "左对齐": "left",
    "右": "right", "右对齐": "right",
}

_VALIGN_VAL: dict[str, str] = {
    "居中": "center", "中间": "center",
    "顶端": "top", "顶部": "top",
    "底端": "bottom", "底部": "bottom",
}

_TRUTHY = {"true", "1", "yes", "on", "是", "加粗"}
_FALSY = {"false", "0", "no", "off", "否", "none", ""}


def _looks_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _is_truthy(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return val != 0
    s = str(val).strip()
    if s in ("是", "加粗"):
        return True
    if s == "否":
        return False
    return s.lower() in _TRUTHY


def _snake_to_camel(key: str) -> str:
    parts = [p for p in str(key).split("_") if p]
    if len(parts) < 2:
        return key
    return parts[0] + "".join(p[:1].upper() + p[1:] for p in parts[1:])


def _normalize_line_spacing_value(val: Any) -> Any:
    """Render guard for bare ``lineSpacing`` numbers.

    - Already has ``x`` / ``pt`` / ``%`` → keep (plus CN aliases via ``_LINE_SPACING_VAL``).
    - Bare number **< 3** → multiple (``1``/``1.5``/``2`` → ``1x``/``1.5x``/``2x``).
    - Bare number **> 10** → fixed pt (``12``/``20`` → ``12pt``/``20pt``).
    - **3–10** inclusive → leave unchanged (ambiguous; do not guess).
    """
    if isinstance(val, bool) or val is None:
        return val

    if isinstance(val, (int, float)):
        n = float(val)
        if n < 3:
            if n == int(n):
                return f"{int(n)}x"
            s = str(n).rstrip("0").rstrip(".")
            return f"{s}x"
        if n > 10:
            if n == int(n):
                return f"{int(n)}pt"
            s = str(n).rstrip("0").rstrip(".")
            return f"{s}pt"
        return val

    s = str(val).strip()
    if not s:
        return val
    mapped = _LINE_SPACING_VAL.get(s) or _LINE_SPACING_VAL.get(s.lower())
    if mapped:
        return mapped
    # Already unit-qualified
    if re.search(r"(x|pt|%)$", s, flags=re.I):
        return val
    if re.fullmatch(r"\d+(\.\d+)?", s):
        n = float(s)
        if n < 3:
            return f"{s}x"
        if n > 10:
            return f"{s}pt"
        return val
    return val


def _normalize_align_value(val: Any) -> Any:
    if not isinstance(val, str):
        return val
    s = val.strip()
    return _ALIGN_VAL.get(s) or _ALIGN_VAL.get(s.lower()) or s


def _normalize_size_value(val: Any) -> Any:
    if isinstance(val, (int, float)) and float(val) in {22, 18, 16, 15, 14, 12, 10.5, 9}:
        n = float(val)
        return "10.5pt" if n == 10.5 else f"{int(n) if n == int(n) else n}pt"
    if not isinstance(val, str):
        return val
    s = val.strip()
    if s in _SIZE_CN:
        return _SIZE_CN[s]
    if re.fullmatch(r"(22|18|16|15|14|12|9)(\.0)?", s):
        return f"{int(float(s))}pt"
    if s in ("10.5", "10.50"):
        return "10.5pt"
    return val


def _normalize_boolish(val: Any) -> Any:
    if isinstance(val, bool):
        return val
    s = str(val).strip().lower()
    if s in _TRUTHY or str(val).strip() in ("是", "加粗"):
        return True
    if s in _FALSY or str(val).strip() == "否":
        return False
    return val


_BORDER_STYLES = frozenset({
    "single", "double", "dashed", "dotted", "thick", "thin", "wave",
    "doublewave", "dashsmallgap", "dotdash", "dotdotdash", "triple",
    "outset", "inset", "dashdotstroked", "threedemboss", "threedengrave",
    "none", "nil",
})


def _is_border_size_token(s: str) -> bool:
    t = str(s or "").strip().lower()
    return bool(re.fullmatch(r"\d+(\.\d+)?(pt|cm|mm)?", t))


def _normalize_border_value(val: Any) -> Any:
    """LLM/extract often emit a bare sz (``4`` / ``12``). officecli needs STYLE;SIZE;COLOR."""
    if val is None or isinstance(val, (dict, list, bool)):
        return val
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        if val == 0:
            return "nil"
        sz = str(int(val) if float(val).is_integer() else val)
        return f"single;{sz};auto"
    s = str(val).strip()
    if not s:
        return val
    compact = re.sub(r"\s+", "", s).lower()
    if "bordervalues" in compact:
        return "nil"
    low = s.lower()
    if low in ("none", "false"):
        return "none"
    if low in ("nil", "0"):
        return "nil"
    if ";" in s:
        parts = [p.strip() for p in s.split(";") if p.strip() != ""]
        if not parts:
            return "nil"
        head = parts[0]
        if _is_border_size_token(head):
            color = parts[1] if len(parts) > 1 else "auto"
            return f"single;{head};{color}"
        return s
    if _is_border_size_token(s):
        return f"single;{s};auto"
    if low.replace(" ", "") in _BORDER_STYLES:
        return s
    # pptx-ish "1pt solid 808080"
    bits = s.split()
    if len(bits) >= 2 and _is_border_size_token(bits[0]):
        dash = bits[1]
        color = bits[2] if len(bits) > 2 else "auto"
        style = "single" if dash.lower() in ("solid", "single") else dash
        return f"{style};{bits[0]};{color}"
    return s


def _fold_border_sz_keys(props: dict[str, Any]) -> int:
    """Merge ``border.bottom.sz`` / ``.color`` into ``border.bottom`` (officecli GET split)."""
    n = 0
    for side in ("all", "top", "bottom", "left", "right"):
        base = f"border.{side}"
        sz = props.pop(f"{base}.sz", None)
        color = props.pop(f"{base}.color", None)
        if sz is not None:
            n += 1
        if color is not None:
            n += 1
        cur = props.get(base)
        col = color if color not in (None, "") else "auto"
        if cur in (None, "", "none") and sz not in (None, ""):
            props[base] = f"single;{sz};{col}"
            n += 1
        elif isinstance(cur, str) and ";" not in cur and _is_border_size_token(cur):
            props[base] = f"single;{cur};{col if color not in (None, '') else 'auto'}"
            n += 1
        elif (
            isinstance(cur, str)
            and cur.lower() in (_BORDER_STYLES - {"none", "nil"})
            and sz not in (None, "")
            and ";" not in cur
        ):
            props[base] = f"{cur};{sz};{col}"
            n += 1
    return n


def _assign_if_empty(props: dict[str, Any], key: str, val: Any) -> bool:
    if key not in props or props[key] in (None, "", "none"):
        props[key] = val
        return True
    return False


def _flatten_nested_dotted_group(props: dict[str, Any], group: str) -> int:
    """``border: {all: none}`` → ``border.all=none`` (officecli dotted keys)."""
    nested = props.get(group)
    if isinstance(nested, str) and nested.strip():
        props.pop(group, None)
        _assign_if_empty(props, f"{group}.all", nested.strip())
        return 1
    if not isinstance(nested, dict):
        return 0
    n = 0
    props.pop(group, None)
    for sub, val in nested.items():
        if val in (None, ""):
            continue
        dotted = f"{group}.{sub}"
        if _assign_if_empty(props, dotted, val):
            n += 1
        else:
            n += 1
    return n


# Paragraph/run keys that LLM sometimes copies onto section page-setup.
_SECTION_FORBIDDEN = frozenset({
    "pageBreakBefore", "outlineLvl", "keepNext", "keepLines", "widowControl",
    "firstLineIndent", "hangingIndent", "lineSpacing", "spaceBefore", "spaceAfter",
    "listStyle", "numLevel", "numId", "bold", "italic", "size", "align",
    "font", "font.ea", "font.latin", "color", "valign", "hAlign",
})
SECTION_OFFICECLI_KEYS = frozenset({
    "marginTop", "marginBottom", "marginLeft", "marginRight",
    "marginHeader", "marginFooter", "orientation", "columns", "columnSpace",
    "pgBorders", "type", "pageNumFmt", "pageStart", "titlePage",
})
TABLE_FORMAT_KEYS = frozenset({
    "border.all", "border.top", "border.bottom", "border.left", "border.right",
    "align", "width", "layout", "repeat_header",
})
CELL_FORMAT_KEYS = frozenset({
    "border.all", "border.top", "border.bottom", "border.left", "border.right",
    "fill", "valign",
})
IMAGE_FORMAT_KEYS = frozenset({"width", "height", "hAlign"})
_IMAGE_CROP_KEYS = frozenset({
    "crop", "cropLeft", "cropTop", "cropRight", "cropBottom",
    "cropleft", "croptop", "cropright", "cropbottom",
})


def strip_wrong_layer_keys(props: dict[str, Any], *, obj: str) -> int:
    """Drop keys that are valid officecli elsewhere but not on this object."""
    if not isinstance(props, dict):
        return 0
    n = 0
    if obj == "section":
        if _is_truthy(props.get("pageBreakBefore")) and not props.get("type"):
            props["type"] = "nextPage"
            n += 1
        allowed = SECTION_OFFICECLI_KEYS
        forbidden = _SECTION_FORBIDDEN
        for k in list(props):
            if k in forbidden or k not in allowed:
                props.pop(k, None)
                n += 1
        return n
    if obj == "table_format":
        allowed = TABLE_FORMAT_KEYS
    elif obj == "cell_format":
        allowed = CELL_FORMAT_KEYS
    elif obj == "image":
        if props.get("align") and not props.get("hAlign"):
            props["hAlign"] = props.pop("align")
            n += 1
        for k in list(props):
            if k in _IMAGE_CROP_KEYS or str(k).lower().startswith("crop"):
                props.pop(k, None)
                n += 1
                continue
            if k not in IMAGE_FORMAT_KEYS:
                props.pop(k, None)
                n += 1
        return n
    else:
        return 0
    for k in list(props):
        if k not in allowed:
            props.pop(k, None)
            n += 1
    return n


def coerce_officecli_prop_keys(props: dict[str, Any] | None) -> int:
    """In-place 护栏：focus/snake_case 键与常见取值 → officecli 键/值。

    Recurses into nested dicts (table_format / cells / header). Existing
    camelCase wins on key conflict. Returns the number of rewrites.

    Render-time length guards (also applied via ``apply_render_prop_guards``):
      - ``margin*`` bare numbers → ``cm`` (e.g. ``2`` → ``2cm``)
      - ``lineSpacing`` bare ``<3`` → ``x``; bare ``>10`` → ``pt``; ``3..10`` unchanged
    """
    if not isinstance(props, dict):
        return 0
    n = 0
    for k in list(props):
        if k in _IMAGE_CROP_KEYS or (isinstance(k, str) and k.lower().startswith("crop")):
            props.pop(k, None)
            n += 1

    if "font_family" in props:
        val = props.pop("font_family")
        n += 1
        if isinstance(val, str) and val.strip():
            target = "font.ea" if _looks_cjk(val) else "font.latin"
            if _assign_if_empty(props, target, val.strip()):
                pass
        elif isinstance(val, dict):
            nested = dict(val)
            n += coerce_officecli_prop_keys(nested)
            if nested.get("latin") and _assign_if_empty(props, "font.latin", nested["latin"]):
                pass
            if nested.get("ea") and _assign_if_empty(props, "font.ea", nested["ea"]):
                pass

    if "run_font_family" in props:
        val = props.pop("run_font_family")
        n += 1
        if isinstance(val, str) and val.strip():
            target = "font.ea" if _looks_cjk(val) else "font.latin"
            _assign_if_empty(props, target, val.strip())

    for old, (new, encoded) in list(PROP_FLAG_ALIASES.items()):
        if old not in props:
            continue
        val = props.pop(old)
        n += 1
        if isinstance(val, str) and val.strip() and val.strip().lower() not in _TRUTHY | _FALSY:
            # already an officecli-ish value (e.g. valign key with "center")
            if _assign_if_empty(props, new, val):
                continue
        if _is_truthy(val):
            _assign_if_empty(props, new, encoded)

    for old, new in list(PROP_KEY_ALIASES.items()):
        if old not in props:
            continue
        val = props.pop(old)
        n += 1
        _assign_if_empty(props, new, val)

    for old in list(props.keys()):
        if not isinstance(old, str) or "_" not in old or old in PROP_KEY_ALIASES:
            continue
        if old in ("font_family", "run_font_family") or old in PROP_FLAG_ALIASES:
            continue
        camel = _snake_to_camel(old)
        if camel in _OFFICECLI_KEYS and camel != old:
            val = props.pop(old)
            n += 1
            _assign_if_empty(props, camel, val)

    if "lineSpacing" in props:
        fixed = _normalize_line_spacing_value(props["lineSpacing"])
        if fixed != props["lineSpacing"]:
            props["lineSpacing"] = fixed
            n += 1
    if "size" in props:
        fixed = _normalize_size_value(props["size"])
        if fixed != props["size"]:
            props["size"] = fixed
            n += 1
    for ak in ("align", "hAlign"):
        if ak in props:
            fixed = _normalize_align_value(props[ak])
            if fixed != props[ak]:
                props[ak] = fixed
                n += 1
    if "valign" in props and isinstance(props["valign"], str):
        mapped = _VALIGN_VAL.get(props["valign"].strip()) or _VALIGN_VAL.get(
            props["valign"].strip().lower()
        )
        if mapped and mapped != props["valign"]:
            props["valign"] = mapped
            n += 1
    for bk in ("bold", "italic", "keepNext", "pageBreakBefore", "titlePage",
               "superscript", "subscript", "caps", "smallcaps", "repeat_header"):
        if bk in props:
            fixed = _normalize_boolish(props[bk])
            if fixed != props[bk]:
                props[bk] = fixed
                n += 1
    # Section break aliases only (leave header type=first alone)
    if "type" in props and isinstance(props["type"], str):
        raw = props["type"].strip()
        key = "".join(c for c in raw.lower() if c.isalnum())
        if key in {
            "newpage", "nextpage", "pagebreak", "breaknewpage",
            "evenpage", "oddpage", "nextcolumn", "continuous",
        }:
            fixed = sanitize_section_break_type(raw)
            if fixed != props["type"]:
                props["type"] = fixed
                n += 1
        else:
            for ok in ("nextPage", "continuous", "evenPage", "oddPage", "nextColumn"):
                if ok.lower() == raw.lower() and ok != raw:
                    props["type"] = ok
                    n += 1
                    break
    for k in ("firstLineIndent", "hangingIndent", "indent", "rightIndent",
              "spaceBefore", "spaceAfter"):
        if k in props:
            fixed = _normalize_len_for_officecli(props[k])
            if fixed != props[k]:
                props[k] = fixed
                n += 1
    for k in _MARGIN_LEN_KEYS:
        if k in props:
            fixed = _normalize_margin_value(props[k])
            if fixed != props[k]:
                props[k] = fixed
                n += 1

    if "borders" in props and "border" not in props:
        props["border"] = props.pop("borders")
        n += 1
    n += _flatten_nested_dotted_group(props, "border")
    n += _flatten_nested_dotted_group(props, "font")
    n += _fold_border_sz_keys(props)
    for k in list(props):
        if str(k).startswith("border.") and "." not in str(k)[7:]:
            # border.all / border.top — not border.bottom.sz (already folded)
            fixed = _normalize_border_value(props[k])
            if fixed != props[k]:
                props[k] = fixed
                n += 1

    for key in ("table_format",):
        child = props.get(key)
        if isinstance(child, dict):
            n += coerce_officecli_prop_keys(child)
    cells = props.get("cells")
    if isinstance(cells, dict):
        for cf in cells.values():
            if isinstance(cf, dict):
                n += coerce_officecli_prop_keys(cf)
    return n


def apply_render_prop_guards(props: dict[str, Any] | None) -> dict[str, Any]:
    """Copy of *props* after officecli render guards (default for all docx render paths).

    Used by ``render_template_docx.render`` (step5/step6 / pipeline_auto /
    pipeline_overlapped / rerender_docx_only) so margin / lineSpacing rules stay
    consistent everywhere Word is written.
    """
    out = dict(props or {})
    coerce_officecli_prop_keys(out)
    return out


def invert_whitelist_focus(wl_block: dict[str, Any] | None) -> dict[str, list[str]]:
    """format_focus tag → officecli keys listed in one whitelist block."""
    inv: dict[str, list[str]] = {}
    for key, meta in (wl_block or {}).items():
        if not isinstance(meta, dict):
            continue
        for tag in meta.get("focus") or []:
            inv.setdefault(str(tag), []).append(str(key))
    return inv


def map_focus_tags_to_officecli_keys(
    tags: list[Any] | None,
    wl_block: dict[str, Any] | None,
) -> list[str]:
    """Rewrite role-library focus tags to officecli prop keys (stable order, de-duped)."""
    inv = invert_whitelist_focus(wl_block)
    officecli_keys = set((wl_block or {}).keys())
    out: list[str] = []
    seen: set[str] = set()
    for raw in tags or []:
        tag = str(raw)
        mapped = list(inv.get(tag) or [])
        if not mapped and tag in PROP_KEY_ALIASES:
            mapped = [PROP_KEY_ALIASES[tag]]
        if not mapped and tag in officecli_keys:
            mapped = [tag]
        for key in mapped:
            if key not in seen:
                seen.add(key)
                out.append(key)
    return out


def apply_officecli_keys_to_selected_roles(selected: dict[str, Any], wl: dict[str, Any]) -> None:
    """In-place: selected_roles focus lists become officecli keys for the step4 prompt."""
    para = wl.get("paragraph") or {}
    section = wl.get("section") or {}
    run = wl.get("run") or {}
    image = wl.get("image") or {}
    table_format = wl.get("table_format") or {}
    cell_format = wl.get("cell_format") or {}
    for item in selected.get("paragraph") or []:
        if isinstance(item, dict):
            item["effective_focus"] = map_focus_tags_to_officecli_keys(
                item.get("effective_focus"), para,
            )
    for item in selected.get("section") or []:
        if isinstance(item, dict):
            item["effective_focus"] = map_focus_tags_to_officecli_keys(
                item.get("effective_focus"), section,
            )
    for item in selected.get("image") or []:
        if isinstance(item, dict):
            item["effective_focus"] = map_focus_tags_to_officecli_keys(
                item.get("effective_focus"), image,
            )
    for item in selected.get("run") or []:
        if isinstance(item, dict):
            item["format_focus"] = map_focus_tags_to_officecli_keys(
                item.get("format_focus"), run,
            )
    for item in selected.get("table") or []:
        if not isinstance(item, dict):
            continue
        item["table_focus"] = map_focus_tags_to_officecli_keys(
            item.get("table_focus"), table_format,
        )
        for _pos, meta in (item.get("positions") or {}).items():
            if isinstance(meta, dict):
                meta["cell_focus"] = map_focus_tags_to_officecli_keys(
                    meta.get("cell_focus"), cell_format,
                )


def drop_unlisted_officecli_props(role_params: dict[str, Any], wl: dict[str, Any]) -> list[str]:
    """Drop prop keys not in officecli_whitelist. Returns notes like ``role(obj): [key]``."""
    notes: list[str] = []
    tbl_keys = set((wl.get("table_format") or {}).keys())
    cell_keys = set((wl.get("cell_format") or {}).keys())
    by_obj = {
        "paragraph": set((wl.get("paragraph") or {}).keys()),
        "run": set((wl.get("run") or {}).keys()),
        "section": set((wl.get("section") or {}).keys()),
        "image": set((wl.get("image") or {}).keys()),
    }
    for role, spec in (role_params or {}).items():
        if not isinstance(spec, dict):
            continue
        obj = spec.get("object", "")
        props = spec.get("props")
        if not isinstance(props, dict):
            continue
        if obj == "table":
            tf = props.get("table_format")
            if isinstance(tf, dict):
                bad = [k for k in list(tf) if k not in tbl_keys]
                for k in bad:
                    tf.pop(k, None)
                if bad:
                    notes.append(f"{role}.table_format: {bad}")
            for pos, cf in (props.get("cells") or {}).items():
                if not isinstance(cf, dict):
                    continue
                badc = [k for k in list(cf) if k not in cell_keys]
                for k in badc:
                    cf.pop(k, None)
                if badc:
                    notes.append(f"{role}.cells.{pos}: {badc}")
            continue
        allowed = by_obj.get(str(obj))
        if not allowed:
            continue
        bad = [k for k in list(props) if k not in allowed]
        for k in bad:
            props.pop(k, None)
        if bad:
            notes.append(f"{role}({obj}): {bad}")
    return notes


def expand_font_props(props: dict[str, Any] | None) -> dict[str, Any]:
    """Accept both dotted ``font.latin`` / ``font.ea`` and nested ``font: {latin, ea}``.

    Returns a shallow copy. Nested ``font`` objects are expanded then removed so
    renderers and officecli see only string family keys.
    """
    out = dict(props or {})
    font = out.get("font")
    if isinstance(font, dict):
        latin = font.get("latin") or font.get("ascii") or font.get("hAnsi")
        ea = font.get("ea") or font.get("eastAsia") or font.get("east_asia")
        if latin and not out.get("font.latin"):
            out["font.latin"] = latin
        if ea and not out.get("font.ea"):
            out["font.ea"] = ea
        out.pop("font", None)
    return out


def font_family_of(props: dict[str, Any] | None) -> str | None:
    """Single family name for officecli ``font`` (headers/cells): ea then latin."""
    p = expand_font_props(props)
    for key in ("font.ea", "font.latin", "font"):
        v = p.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def style_props_for_paragraph(props: dict[str, Any] | None) -> dict[str, Any]:
    """Props suitable to put on a Word paragraph style (not list ids).

    officecli quirk: paragraph direct format uses ``indent`` for left indent, but
    ``/styles/{id}`` rejects ``indent`` and wants ``leftIndent`` instead.
    """
    raw = dict(props or {})
    coerce_officecli_prop_keys(raw)
    props = expand_font_props(raw)
    # officecli style element rejects some paragraph-only keys
    skip = {
        "listStyle", "numId", "numLevel",
        "keepNext", "keepLines", "pageBreakBefore", "widowControl",
    }
    len_keys = {
        "firstLineIndent", "hangingIndent", "indent", "rightIndent",
        "leftIndent", "spaceBefore", "spaceAfter",
    }
    out = {}
    for k, v in (props or {}).items():
        if v in (None, "", "none") or k in skip:
            continue
        if k == "lineSpacing":
            out[k] = _normalize_line_spacing_value(v)
            continue
        # Paragraph pool / whitelist key ``indent`` → style key ``leftIndent``
        if k == "indent":
            out["leftIndent"] = _normalize_len_for_officecli(v)
            continue
        if k in len_keys:
            out[k] = _normalize_len_for_officecli(v)
            continue
        if k in _MARGIN_LEN_KEYS:
            out[k] = _normalize_margin_value(v)
            continue
        out[k] = v
    return out


def char_props(props: dict[str, Any] | None) -> dict[str, Any]:
    keys = (
        "font.latin", "font.ea", "font", "size", "bold", "italic", "color",
        "underline", "superscript", "subscript", "caps", "smallcaps",
    )
    props = expand_font_props(props)
    return {k: v for k, v in props.items() if k in keys and v not in (None, "", "none")}


def build_outline_and_maps(
    plan: dict[str, Any],
    role_params: dict[str, Any],
    *,
    pos_para: dict[str, dict[str, str]] | None = None,
) -> tuple[dict, dict, dict]:
    """Flatten plan → outline.json objects + styles.json + map.json."""
    pos_para = pos_para or {}
    sections = plan.get("sections") or []
    outline_entries = plan.get("outline") or []

    styles: dict[str, Any] = {
        "section": {},
        "paragraph": {},
        "table_structure": {},
        "table_cell": {},
        "image": {},
        "run": {},
    }
    maps: dict[str, Any] = {
        "section": {},
        "paragraph": {},
        "table_structure": {},
        "table_cell": {},
        "image": {},
        "runs": {},
        "paragraph_objects": {},
    }

    objects: list[dict[str, Any]] = []
    sec_i = 0
    tbl_i = 0
    img_i = 0
    para_i = 0
    obj_i = 0

    def remember_style(kind: str, role_id: str, locator: Any) -> None:
        bucket = styles[kind]
        if role_id not in bucket:
            bucket[role_id] = locator
        maps[kind].setdefault(role_id, [])
        if locator not in maps[kind][role_id]:
            maps[kind][role_id].append(locator)

    # section exemplars from plan.sections order (1-based)
    for si, sec in enumerate(sections, start=1):
        rid = sec.get("role_id") or f"Sec{si}"
        remember_style("section", rid, si)

    for entry in outline_entries:
        inst = entry.get("instance_id") or ""
        sec_meta = next((s for s in sections if s.get("instance_id") == inst), None)
        if sec_meta is None and sec_i < len(sections):
            sec_meta = sections[sec_i]
        sec_i += 1
        sec_index = sec_i
        sec_role = (sec_meta or {}).get("role_id") or f"Sec{sec_index}"

        objects.append(
            {
                "index": obj_i,
                "object_id": f"sec_{inst or sec_index}",
                "kind": "section_break",
                "role_id": sec_role,
                "section_index": sec_index,
                "instance_id": inst,
                "main_point": (sec_meta or {}).get("title") or sec_role,
            }
        )
        obj_i += 1

        for block in entry.get("blocks") or []:
            rid = block.get("role_id") or ""
            bid = block.get("block_id") or f"B{obj_i:03d}"
            kind = role_kind(rid)
            mp = block.get("main_point") or ""
            base = {
                "index": obj_i,
                "object_id": bid,
                "role_id": rid,
                "section_index": sec_index,
                "instance_id": inst,
                "main_point": mp,
                "block_id": bid,
            }

            if kind == "table":
                tbl_i += 1
                remember_style("table_structure", rid, tbl_i)
                cells = []
                pmap = pos_para.get(rid) or {}
                # fallback label/value or header/stub/data
                if not pmap:
                    spec = (role_params.get(rid) or {})
                    # keep empty; render will use defaults
                row = 1
                for pos, cell_role in (pmap or {"label": "ParaTblCoverLabel", "value": "ParaTblCoverValue"}).items():
                    if not cell_role:
                        continue
                    locator = {"table": tbl_i, "row": row, "col": 1 if pos in ("label", "stub", "header") else 2}
                    # simpler: assign sequential for exemplar only
                    cells.append({"pos": pos, "role_id": cell_role})
                    if cell_role not in styles["table_cell"]:
                        # first cell role gets (table,1,1) style exemplar; refine in render
                        styles["table_cell"][cell_role] = {"table": tbl_i, "row": 1, "col": 1}
                    maps["table_cell"].setdefault(cell_role, [])
                    coord = f"{tbl_i}:1:1"
                    if pos in ("value", "data"):
                        coord = f"{tbl_i}:1:2"
                    if pos == "header":
                        coord = f"{tbl_i}:1:1"
                    if coord not in maps["table_cell"][cell_role]:
                        maps["table_cell"][cell_role].append(coord)
                objects.append({**base, "kind": "table", "table_index": tbl_i, "cells": cells})
            elif kind == "image":
                img_i += 1
                remember_style("image", rid, img_i)
                objects.append({**base, "kind": "image", "image_index": img_i, "figure_id": block.get("figure_id")})
            else:
                # paragraph (default)
                para_i += 1
                remember_style("paragraph", rid, {"style_id": rid, "para_index": para_i})
                maps["paragraph_objects"][bid] = {
                    "para_index": para_i,
                    "style_id": rid,
                    "section_index": sec_index,
                }
                runs_spec = []
                text = str(mp)
                for er in block.get("expected_runs") or []:
                    role = er.get("role") if isinstance(er, dict) else None
                    sub = (er.get("text") if isinstance(er, dict) else None) or ""
                    if not role:
                        continue
                    if sub and sub in text:
                        start = text.find(sub)
                        end = start + len(sub)
                    else:
                        # fallback: mark prefix length from example
                        ex = ((role_params.get(role) or {}).get("example") or "")[:6]
                        start, end = 0, min(len(text), max(1, len(ex)))
                        sub = text[start:end]
                    runs_spec.append({"role": role, "start": start, "end": end, "text": sub})
                    if role not in styles["run"]:
                        styles["run"][role] = {"object_id": bid, "start": start, "end": end}
                if runs_spec:
                    maps["runs"][bid] = [{"role": r["role"], "start": r["start"], "end": r["end"]} for r in runs_spec]
                objects.append({**base, "kind": "paragraph", "para_index": para_i, "expected_runs": runs_spec})
            obj_i += 1

    outline = {
        "meta": {
            "genre": (plan.get("meta") or {}).get("genre"),
            "theme": (plan.get("meta") or {}).get("theme"),
            "locale": (plan.get("meta") or {}).get("locale"),
        },
        "sections": [
            {
                "section_index": i + 1,
                "instance_id": s.get("instance_id"),
                "role_id": s.get("role_id"),
                "title": s.get("title") or s.get("role_id"),
            }
            for i, s in enumerate(sections)
        ],
        "objects": objects,
    }
    return outline, styles, maps


def sanitize_section_break_type(value: Any) -> str:
    """Normalize section break ``type`` for officecli (e.g. ``newPage`` → ``nextPage``)."""
    ok = ("nextPage", "continuous", "evenPage", "oddPage", "nextColumn")
    aliases = {
        "newpage": "nextPage",
        "nextpage": "nextPage",
        "next_page": "nextPage",
        "pagebreak": "nextPage",
        "page_break": "nextPage",
        "breaknewpage": "nextPage",
        "continuous": "continuous",
        "evenpage": "evenPage",
        "even_page": "evenPage",
        "oddpage": "oddPage",
        "odd_page": "oddPage",
        "nextcolumn": "nextColumn",
        "next_column": "nextColumn",
    }
    if value is None:
        return "nextPage"
    s = str(value).strip()
    if not s or s.lower() == "none":
        return "nextPage"
    if s in ok:
        return s
    for cand in ok:
        if cand.lower() == s.lower():
            return cand
    key = "".join(c for c in s.lower() if c.isalnum())
    return aliases.get(key, "nextPage")


def normalize_section_role_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Stable key order so header/footer sit above page ``props`` (easy to find).

    Section chrome is sibling keys ``header`` / ``footer`` / ``header_first``,
    not inside ``props`` (``props`` = /section page layout only).
    """
    out: dict[str, Any] = {"object": entry.get("object") or "section"}
    if entry.get("display_name"):
        out["display_name"] = entry["display_name"]
    if entry.get("example") not in (None, ""):
        out["example"] = entry["example"]
    for hf in ("header", "footer", "header_first"):
        if isinstance(entry.get(hf), dict):
            # Keep even when only {"text": ""} — empty header is a deliberate human edit
            out[hf] = dict(entry[hf])
    props = {
        k: v for k, v in (entry.get("props") or {}).items()
        if v not in (None, "", "none")
        and not str(k).startswith(("headerRef", "footerRef"))
        and k != "sectionType"
    }
    if "type" in props:
        props["type"] = sanitize_section_break_type(props["type"])
    out["props"] = props
    if entry.get("cells"):
        out["cells"] = entry["cells"]
    return out


_TABLE_MEASURE_NOISE = {
    "cols", "rows", "_gridCols", "colWidths", "width",
    # Flattened officecli readbacks that fight intentional table_format
    "border.top", "border.top.sz", "border.bottom", "border.bottom.sz",
    "border.left", "border.left.sz", "border.right", "border.right.sz",
    "border.insideH", "border.insideH.sz", "border.insideV", "border.insideV.sz",
}

_CELL_CHROME_KEYS = (
    "valign", "align", "fill", "shading",
    "border.all", "border.top", "border.bottom", "border.left", "border.right",
    "border.insideH", "border.insideV",
)


def table_format_of(entry: dict[str, Any] | None) -> dict[str, Any]:
    """Layer 1 — table-structure props from a Tbl* style_tree entry."""
    entry = entry or {}
    props = entry.get("props") or {}
    tf = entry.get("table_format")
    if isinstance(tf, dict) and tf:
        src = dict(tf)
    else:
        nested = props.get("table_format")
        if isinstance(nested, dict) and nested:
            src = dict(nested)
        else:
            src = {
                k: v for k, v in props.items()
                if v is not None and v != ""
                and k not in ("cells", "table_format")
                and k not in _TABLE_MEASURE_NOISE
                and (k.startswith("border") or k in ("align", "width", "layout", "repeat_header"))
            }
    coerce_officecli_prop_keys(src)
    strip_wrong_layer_keys(src, obj="table_format")
    return {
        k: v for k, v in src.items()
        if v is not None and v != "" and not isinstance(v, (dict, list))
    }


def table_cells_chrome_of(entry: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Layer 2 — table-cell chrome keyed by position (label/header/…)."""
    entry = entry or {}
    props = entry.get("props") or {}
    raw = entry.get("cells") if isinstance(entry.get("cells"), dict) else None
    if raw is None:
        raw = props.get("cells") if isinstance(props.get("cells"), dict) else {}
    out: dict[str, dict[str, Any]] = {}
    for pos, chrome in (raw or {}).items():
        if not isinstance(chrome, dict):
            continue
        cleaned = dict(chrome)
        coerce_officecli_prop_keys(cleaned)
        strip_wrong_layer_keys(cleaned, obj="cell_format")
        cleaned = {
            k: v for k, v in cleaned.items()
            if v is not None and v != "" and not isinstance(v, (dict, list)) and (
                k in _CELL_CHROME_KEYS or str(k).startswith("border")
            )
        }
        if cleaned:
            out[str(pos)] = cleaned
    return out


def normalize_table_role_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Three-layer table style: table_format + cells(chrome) + (ParaTbl text separate).

    ``Tbl*`` stores structure + per-pos cell chrome. Grids' paragraph styles live on
    ``ParaTbl*`` roles (object=paragraph), not inside this entry.
    """
    props_in = dict(entry.get("props") or {})
    tf = table_format_of(entry)
    cells = table_cells_chrome_of(entry)
    # Drop nested copies / measure noise from leftover props
    leftovers = {
        k: v for k, v in props_in.items()
        if k not in ("table_format", "cells")
        and k not in _TABLE_MEASURE_NOISE
        and k not in tf
        and v not in (None, "", "none")
    }
    out: dict[str, Any] = {"object": "table"}
    if entry.get("display_name"):
        out["display_name"] = entry["display_name"]
    if entry.get("example") not in (None, ""):
        out["example"] = entry["example"]
    if tf:
        out["table_format"] = tf
    if cells:
        out["cells"] = cells
    # Keep empty props only if leftovers; prefer clean shape without nested chrome
    if leftovers:
        out["props"] = leftovers
    return out


def style_tree_from_role_params(
    role_params: dict[str, Any],
    *,
    section_titles: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build style_tree; inject default header/footer for sections (as role preview render does).

    Note: role_params from pipeline_role usually omit header/footer — preview synthesizes
    them at render time. Seed style_tree must materialize them as first-class fields.

    Tables: lift ``table_format`` + ``cells`` out of nested ``props`` so the three layers
    (structure / cell chrome / ParaTbl paragraph) are explicit.
    """
    section_titles = section_titles or {}
    tree: dict[str, Any] = {"roles": {}}
    for rid, spec in (role_params or {}).items():
        if not isinstance(spec, dict):
            continue
        kind = spec.get("object") or role_kind(rid)
        entry: dict[str, Any] = {
            "object": kind,
            "props": dict(spec.get("props") or {}),
            "example": spec.get("example") or "",
            "display_name": spec.get("display_name") or rid,
        }
        for hf in ("header", "footer", "header_first"):
            if spec.get(hf):
                entry[hf] = dict(spec[hf]) if isinstance(spec[hf], dict) else spec[hf]
        if spec.get("cells"):
            entry["cells"] = spec["cells"]
        if spec.get("table_format"):
            entry["table_format"] = spec["table_format"]

        # Section chrome: pipeline_role preview synthesizes these at render time even when
        # role_params omit them — seedgen must materialize them in style_tree for humans.
        if rid.startswith("Sec") or kind == "section":
            title = (
                section_titles.get(rid)
                or entry.get("example")
                or entry.get("display_name")
                or rid
            )
            # Prefer short title without trailing 「节」 for header text
            title_s = str(title).rstrip("节") if str(title).endswith("节") else str(title)
            props = entry.get("props") or {}
            if not entry.get("header"):
                entry["header"] = {
                    "text": title_s,
                    "align": "center",
                    "size": "9pt",
                    "color": "#808080",
                }
            if not entry.get("footer"):
                entry["footer"] = {
                    "field": "page",
                    "align": "center",
                    "size": "9pt",
                    "color": "#808080",
                }
            if props.get("titlePage") and not entry.get("header_first"):
                entry["header_first"] = {
                    "type": "first",
                    "text": "",
                    "align": "center",
                    "size": "9pt",
                    "color": "#B0B0B0",
                }
            entry = normalize_section_role_entry(entry)
        elif rid.startswith("Tbl") or kind == "table":
            entry = normalize_table_role_entry(entry)
        elif (
            rid.startswith("ParaTbl")
            or kind in {"table_cell", "paragraph.table_cell", "paragraph.cell_text"}
            or ("Cell" in rid and rid.startswith("Para"))
        ):
            # table-paragraph layer: Word named style on in-cell text
            entry["object"] = "paragraph.table_cell"
            entry["in_table_cell"] = True
        tree["roles"][rid] = entry
    return tree


def load_pos_para_from_template(template: dict[str, Any]) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for rid, spec in (template.get("roles") or {}).items():
        if not str(rid).startswith("Tbl"):
            continue
        layout = (spec.get("table_layout") or {}) if isinstance(spec, dict) else {}
        pmap = layout.get("position_map") or {}
        if pmap:
            out[rid] = {pos: (m or {}).get("paragraph") for pos, m in pmap.items()}
    # also from selection table_roles in meta-less templates
    return out
