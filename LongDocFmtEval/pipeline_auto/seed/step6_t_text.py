"""Step6/7 · T-side natural-language format requirements (defaults + overrides).

Emits:
  - t_text.md            full format spec (共性先写，例外后写；props 宜为 init→golden 差分)
  - t_style_propose.md   display_name + description, grouped by fine buckets
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from LongDocFmtEval.pipeline_auto.auto_paths import officecli_locale_yaml
from LongDocFmtEval.pipeline_auto.seed.seed_ir import dump_json, load_json
from LongDocFmtEval.pipeline_auto.style_taxonomy import is_table_paragraph_object

_OBJ_ORDER = (
    "section",
    "paragraph_heading",
    "paragraph_body",
    "paragraph_table",
    "run",
    "table",
    "image",
)
_OBJ_TITLE = {
    "section": "节",
    "paragraph_heading": "标题段落",
    "paragraph_body": "正文段落",
    "paragraph_table": "表格内文本段落",
    "run": "行内",
    "table": "表格",
    "image": "图片",
}
_OBJ_TITLE_EN = {
    "section": "Section",
    "paragraph_heading": "Heading paragraph",
    "paragraph_body": "Body paragraph",
    "paragraph_table": "Table-cell paragraph",
    "run": "Run",
    "table": "Table",
    "image": "Image",
}


def object_bucket(
    obj: str = "",
    rid: str = "",
    *,
    heading_ids: set[str] | None = None,
) -> str:
    """Map stylesheet/role object to T-text layers (section / heading / body / cell / …)."""
    o = str(obj or "").strip().lower()
    r = str(rid or "")
    if o.startswith("section") or r.startswith("Sec"):
        return "section"
    if o.startswith("run") or r.startswith("Run"):
        return "run"
    if (o.startswith("table") and "cell" not in o and "paragraph" not in o) or o in {"tbl"} or r.startswith("Tbl"):
        return "table"
    if o.startswith("image") or o.startswith("img") or r.startswith("Img"):
        return "image"
    if is_table_paragraph_object(o, r):
        return "paragraph_table"
    if o.startswith("paragraph") or o in {"para", "cell", "cell_text"} or r.startswith("Para"):
        if heading_ids and r in heading_ids:
            return "paragraph_heading"
        return "paragraph_body"
    return ""

_STYLE_ID_RE = re.compile(r"^(Sec|Para|Run|Tbl|Img)[A-Z0-9]")

_FALLBACK_DISPLAY = {
    "zh": {
        "ParaTblLabelCell": "标签单元格",
        "ParaTblHeaderCell": "表头单元格",
        "ParaTblStubCell": "行名单元格",
        "ParaTblNoteCell": "表注单元格",
        "ParaTblDataCell": "表体单元格",
        "ParaHeading1": "一级标题",
        "ParaHeading2": "二级标题",
        "ParaHeading3": "三级标题",
        "ParaHeading4": "四级标题",
        "ParaHeading5": "五级标题",
        "ParaHeading6": "六级标题",
    },
    "en": {
        "ParaTblLabelCell": "Label cell",
        "ParaTblHeaderCell": "Header cell",
        "ParaTblStubCell": "Stub cell",
        "ParaTblNoteCell": "Note cell",
        "ParaTblDataCell": "Body cell",
        "ParaHeading1": "Heading 1",
        "ParaHeading2": "Heading 2",
        "ParaHeading3": "Heading 3",
        "ParaHeading4": "Heading 4",
        "ParaHeading5": "Heading 5",
        "ParaHeading6": "Heading 6",
    },
}

_FALLBACK_PURPOSE = {
    "zh": {
        "ParaTblLabelCell": "信息表中的字段标签使用。",
        "ParaTblHeaderCell": "表头、列名、表内标题使用。",
        "ParaTblStubCell": "左侧项目名或指标名使用。",
        "ParaTblNoteCell": "表内附注或续表说明使用。",
        "ParaTblDataCell": "普通数据、说明值使用。",
    },
    "en": {
        "ParaTblLabelCell": "Field labels in key-value / cover info tables.",
        "ParaTblHeaderCell": "Column headers and in-table titles.",
        "ParaTblStubCell": "Row labels or indicator names in the stub column.",
        "ParaTblNoteCell": "In-table notes or continuation remarks.",
        "ParaTblDataCell": "Ordinary data and value cells.",
    },
}

_POS_NAME = {
    "header": "表头行",
    "corner_header": "表头行·表角",
    "column_header": "表头行·列标题",
    "group_header": "表头行·分组",
    "sub_header": "表头行·次级",
    "stub": "行标签列",
    "body_label": "数据行·标签列",
    "body_code": "数据行·编码列",
    "body_value": "数据行·数值列",
    "data_value": "数据行·数值列",
    "code_cell": "数据行·编码列",
    "summary_cell": "数据行·摘要列",
    "body_note": "数据行·备注列",
    "table_note": "表注行",
    "note": "表注行",
    "total_value": "合计行",
    "total": "合计行",
    "label": "标签列",
    "label_col": "标签列",
    "value": "值列",
    "value_col": "值列",
    "photo": "照片格",
}

_POS_NAME_EN = {
    "header": "header row",
    "corner_header": "header · corner",
    "column_header": "header · column",
    "group_header": "header · group",
    "sub_header": "header · sub",
    "stub": "stub column",
    "body_label": "body · label",
    "body_code": "body · code",
    "body_value": "body · value",
    "data_value": "body · value",
    "code_cell": "body · code",
    "summary_cell": "body · summary",
    "body_note": "body · note",
    "table_note": "table note",
    "note": "table note",
    "total_value": "total row",
    "total": "total row",
    "label": "label column",
    "label_col": "label column",
    "value": "value column",
    "value_col": "value column",
    "photo": "photo cell",
}

_LABELS_CACHE: dict[str, dict] = {}


def _labels(locale: str = "zh") -> dict:
    key = "en" if str(locale).lower().startswith("en") else "zh"
    if key not in _LABELS_CACHE:
        path = officecli_locale_yaml("officecli_prop_labels.yaml", locale)
        if path.is_file():
            _LABELS_CACHE[key] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        else:
            _LABELS_CACHE[key] = {}
    return _LABELS_CACHE[key]


def looks_like_style_id(name: str, rid: str = "") -> bool:
    """True if *name* is a machine style_id (ParaTblLabelCell), not a locale display name."""
    n = str(name or "").strip()
    if not n:
        return True
    if rid and n == rid:
        return True
    return bool(_STYLE_ID_RE.match(n))


def public_display_name(rid: str, *candidates: Any, locale: str = "zh") -> str:
    """Natural-language label for T text / template.md. Never returns a raw style_id if avoidable."""
    loc = "en" if str(locale or "").lower().startswith("en") else "zh"
    for c in candidates:
        s = str(c or "").strip()
        if s and not looks_like_style_id(s, rid):
            return s
    mapped = (_FALLBACK_DISPLAY.get(loc) or {}).get(str(rid or ""))
    if mapped:
        return mapped
    if loc == "en":
        body = re.sub(r"^(Sec|Para|Run|Tbl|Img)", "", str(rid or ""))
        words = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", body)
        if words:
            return " ".join(words)
    return ""


def public_purpose(rid: str, *candidates: Any, locale: str = "zh") -> str:
    """Purpose line for T text. Prefer stylesheet/library copy; never leave known cells blank."""
    loc = "en" if str(locale or "").lower().startswith("en") else "zh"
    for c in candidates:
        s = str(c or "").strip()
        if s:
            return s
    return (_FALLBACK_PURPOSE.get(loc) or {}).get(str(rid or "")) or ""


def _san(s: Any) -> str:
    return str(s or "").replace("|", "／").replace("\n", " ").strip()


def _fmt_val(k: str, v: Any, *, locale: str = "zh") -> str:
    val_map = _labels(locale).get("val_labels", {}) or {}
    s = str(v)
    if k in val_map and s in val_map[k]:
        return str(val_map[k][s])
    if k == "lineSpacing" and s.endswith("x"):
        return f"{s[:-1]}x" if str(locale).lower().startswith("en") else f"{s[:-1]}倍"
    if k == "columns":
        return f"{s} columns" if str(locale).lower().startswith("en") else f"{s}栏"
    return s


def describe_props(props: dict | None, *, locale: str = "zh") -> str:
    """Locale-native 'Set A to a; Enable C' / 『设置A为a；启用C』."""
    en = str(locale or "").lower().startswith("en")
    prop_label = _labels(locale).get("prop_labels", {}) or {}
    bool_label = _labels(locale).get("bool_labels", {}) or {}
    setters, enables = [], []
    for k, v in (props or {}).items():
        if v in (None, "", "none", False):
            continue
        if k in bool_label:
            if str(v).lower() == "true":
                enables.append(str(bool_label[k]))
            continue
        if isinstance(v, dict):
            continue
        lab = prop_label.get(k, k)
        val = _fmt_val(k, v, locale=locale)
        setters.append(f"{lab} to {val}" if en else f"{lab}为{val}")
    parts = []
    if setters:
        parts.append(("Set " if en else "设置") + (", " if en else "、").join(setters))
    if enables:
        parts.append(("Enable " if en else "启用") + (", " if en else "、").join(enables))
    return ("; " if en else "；").join(parts)


def _bool_state_word(v: Any, *, locale: str = "zh") -> str:
    en = str(locale or "").lower().startswith("en")
    truthy = str(v).lower() in ("true", "1", "yes")
    if en:
        return "on" if truthy else "off"
    return "开启" if truthy else "关闭"


def describe_prop_change(
    prop: str,
    from_v: Any,
    to_v: Any,
    *,
    locale: str = "zh",
) -> str:
    """Single target clause: ``change X to b`` / ``将X修改为b`` (no from→to)."""
    del from_v
    en = str(locale or "").lower().startswith("en")
    labels = _labels(locale)
    prop_labels = labels.get("prop_labels", {}) or {}
    bool_labels = labels.get("bool_labels", {}) or {}
    prop_key = str(prop or "").strip()
    if not prop_key:
        return ""

    if prop_key in bool_labels:
        subject = str(bool_labels[prop_key])
        to_s = _bool_state_word(to_v, locale=locale)
    else:
        subject = str(prop_labels.get(prop_key) or prop_key)
        to_s = _fmt_val(prop_key, to_v, locale=locale)

    if en:
        return f"change {subject} to {to_s}"
    return f"将{subject}修改为{to_s}"


def _flat_props(spec: dict) -> dict[str, Any]:
    """Normalize role props for diffing (section/paragraph/run/image flat; table special)."""
    obj = str(spec.get("object") or "")
    props = dict(spec.get("props") or {})
    if obj == "table":
        return {}  # handled separately
    if obj == "section":
        # header/footer are nested chrome — keep scalar page props only for defaults
        return {
            k: v for k, v in props.items()
            if k not in ("header", "footer", "headers", "footers")
            and not isinstance(v, (dict, list))
        }
    return {k: v for k, v in props.items() if not isinstance(v, (dict, list))}


def _props_equal(a: Any, b: Any) -> bool:
    return str(a).lower() == str(b).lower() if isinstance(a, bool) or isinstance(b, bool) else a == b


def _majority_defaults(items: list[dict[str, Any]], *, min_frac: float = 0.75) -> dict[str, Any]:
    """Keys whose most-common explicit value covers ≥ min_frac of *all* roles.

    Missing key does not count toward the majority (so ``bold`` on 2/3 roles
    will not become a paragraph default).
    """
    from math import ceil

    if not items:
        return {}
    keys: set[str] = set()
    for it in items:
        keys.update(it.keys())
    out: dict[str, Any] = {}
    n = len(items)
    need = max(1, int(ceil(n * min_frac)))
    for k in sorted(keys):
        present = [it[k] for it in items if k in it and it[k] not in (None, "", "none")]
        if len(present) < need:
            continue
        counter: Counter[str] = Counter(str(v) for v in present)
        top_s, top_n = counter.most_common(1)[0]
        if top_n < need:
            continue
        for v in present:
            if str(v) == top_s:
                out[k] = v
                break
    return out


def _diff_props(props: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    return {
        k: v for k, v in props.items()
        if k not in defaults or not _props_equal(v, defaults[k])
    }


def _iter_role_nodes(node: Any):
    """Yield role dicts from list / nested tree (paragraph.body / table_cell, …)."""
    if isinstance(node, dict):
        if node.get("role_id") or node.get("span_role"):
            yield node
        for v in node.values():
            if isinstance(v, (dict, list)):
                yield from _iter_role_nodes(v)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_role_nodes(item)


def _role_meta(role_params: dict, lib: dict | None) -> tuple[dict[str, str], dict[str, str]]:
    """Collect display_name + purpose text.

    Purpose sources (in order, first wins per role):
      role_params description/desc/discriminators
      library display_name / discriminators / description
    """
    name_by: dict[str, str] = {}
    desc_by: dict[str, str] = {}

    def _purpose_of(d: dict) -> str:
        for key in ("description", "desc", "discriminators"):
            val = d.get(key)
            if isinstance(val, list):
                val = "；".join(str(x) for x in val if x)
            if val not in (None, ""):
                return str(val).strip()
        return ""

    for rid, spec in (role_params or {}).items():
        if not isinstance(spec, dict):
            continue
        if spec.get("display_name") and not looks_like_style_id(str(spec["display_name"]), str(rid)):
            name_by[str(rid)] = str(spec["display_name"])
        purpose = _purpose_of(spec)
        if purpose:
            desc_by[str(rid)] = purpose

    for bucket in ((lib or {}).get("roles") or {}).values():
        for r in _iter_role_nodes(bucket):
            if not isinstance(r, dict):
                continue
            rid = str(r.get("role_id") or r.get("span_role") or "")
            if not rid:
                continue
            lib_dn = str(r.get("display_name") or "").strip()
            if lib_dn and not looks_like_style_id(lib_dn, rid):
                name_by[rid] = lib_dn
            purpose = _purpose_of(r)
            if purpose and rid not in desc_by:
                desc_by[rid] = purpose
    return name_by, desc_by


def _pos_para_from_lib(lib: dict | None) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for t in ((lib or {}).get("roles", {}) or {}).get("table") or []:
        rid = t.get("role_id")
        if not rid:
            continue
        tl = t.get("table_layout") or {}
        out[str(rid)] = {
            pos: str((m or {}).get("paragraph") or "")
            for pos, m in (tl.get("position_map") or {}).items()
            if (m or {}).get("paragraph")
        }
    return out


def _describe_table(
    spec: dict,
    pos_para: dict[str, str],
    name_by: dict[str, str],
    *,
    locale: str = "zh",
) -> list[str]:
    en = str(locale).lower().startswith("en")
    props = spec.get("props") or {}
    tf = {k: v for k, v in (props.get("table_format") or {}).items() if k != "repeat_header"}
    rep = str((props.get("table_format") or {}).get("repeat_header", "")).lower() == "true"
    lines = []
    head = describe_props(tf, locale=locale) + (
        ("; Enable repeat header row" if en else "；启用表头跨页重复") if rep else ""
    )
    lines.append(("Table-level: " if en else "表级：") + (head or ("inherit table defaults" if en else "沿用默认表")))
    cells = props.get("cells") or {}
    single = len(cells) == 1
    pos_map = _POS_NAME_EN if en else _POS_NAME
    for pos, cf in cells.items():
        if single:
            who = "All cells" if en else "全表单元格"
        else:
            para_rid = pos_para.get(pos) or ""
            who = (
                pos_map.get(pos)
                or public_display_name(para_rid, name_by.get(para_rid), locale=locale)
                or pos
            )
        body = describe_props(cf if isinstance(cf, dict) else {}, locale=locale) or (
            "inherit defaults" if en else "沿用默认"
        )
        sep = ": " if en else "："
        lines.append(f"{who}{sep}{body}")
    return lines


def _section_chrome_lines(spec: dict, *, locale: str = "zh") -> list[str]:
    en = str(locale).lower().startswith("en")
    props = spec.get("props") or {}
    lines = []
    for key, label in (("header", "Header" if en else "页眉"), ("footer", "Footer" if en else "页脚")):
        chrome = spec.get(key) if isinstance(spec.get(key), dict) else None
        if chrome is None and isinstance(props.get(key), dict):
            chrome = props.get(key)
        if not isinstance(chrome, dict):
            continue
        bits = []
        if chrome.get("text") not in (None, ""):
            bits.append(
                f'text is "{_san(chrome.get("text"))}"' if en else f"文案为「{_san(chrome.get('text'))}」"
            )
        fmt = describe_props({
            k: v for k, v in chrome.items()
            if k != "text" and not isinstance(v, (dict, list))
        }, locale=locale)
        if fmt:
            bits.append(fmt)
        if bits:
            sep = ": " if en else "："
            joiner = "; " if en else "；"
            lines.append(f"{label}{sep}" + joiner.join(bits))
    return lines


def stylesheet_name_desc(stylesheet: list | None) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """From step2 stylesheet entries → style_id maps (internal only).

    Public T text must use display_name + description only — never style_id / expectation.
    Returns: name_by, desc_by, object_by
    """
    name_by: dict[str, str] = {}
    desc_by: dict[str, str] = {}
    object_by: dict[str, str] = {}
    for item in stylesheet or []:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("style_id") or item.get("role_id") or "").strip()
        if not sid:
            continue
        dn = str(item.get("display_name") or "").strip()
        desc = str(item.get("description") or "").strip()
        obj = str(item.get("object") or "").strip()
        if dn and not looks_like_style_id(dn, sid):
            name_by[sid] = dn
        if desc:
            desc_by[sid] = desc
        if obj:
            object_by[sid] = obj
    return name_by, desc_by, object_by


def build_t_text(
    role_params: dict,
    *,
    genre: str = "",
    lib: dict | None = None,
    stylesheet: list | None = None,
    name_by_role: dict[str, str] | None = None,
    desc_by_role: dict[str, str] | None = None,
    locale: str = "",
    heading_ids: set[str] | None = None,
    source_note: str = "",
) -> str:
    """Full text T with document/object defaults then per-role overrides.

    Headings use locale-native display_name (never style_id).
    *role_params* props should ideally be init→target deltas (step7).
    """
    loc = locale or str((lib or {}).get("locale") or "")
    if not loc:
        loc = "en" if _is_en_locale(genre=genre) else "zh"
    en = _is_en_locale(genre=genre, locale=loc)
    name_by, desc_by = _role_meta(role_params, lib)
    ss_name, ss_desc, ss_obj = stylesheet_name_desc(stylesheet)
    name_by.update(ss_name)
    desc_by.update(ss_desc)
    if name_by_role:
        name_by.update({k: v for k, v in name_by_role.items() if v and not looks_like_style_id(v, k)})
    if desc_by_role:
        desc_by.update(desc_by_role)
    pos_para = _pos_para_from_lib(lib)

    if heading_ids is None:
        from LongDocFmtEval.pipeline_auto.seed.step7_format_diff import heading_style_ids  # noqa: PLC0415
        heading_ids = heading_style_ids(stylesheet, role_params)

    def _bucket(rid: str, spec: dict) -> str:
        return object_bucket(
            spec.get("object") or ss_obj.get(rid), rid, heading_ids=heading_ids,
        ) or "?"

    obj_title = _OBJ_TITLE_EN if en else _OBJ_TITLE
    purpose_k = "Purpose" if en else "用途"
    fmt_ex = "Format exceptions" if en else "格式例外"
    lay_ex = "Layout exceptions" if en else "版式例外"
    fmt_k = "Format" if en else "格式"
    lay_k = "Layout" if en else "版式"
    same_fmt = "same as shared defaults for this layer" if en else "同本节共性默认"
    same_lay = "same as shared section defaults" if en else "同本节共性默认"
    no_change = (
        "no further changes needed (already matches target on this init)"
        if en else "无需再改（相对本 init 已与目标一致）"
    )
    inherit_body = "inherit body baseline" if en else "沿用正文基线"
    no_common = "no shared defaults (see each role)" if en else "无显著共性（各角色单独说明）"
    see_roles = "(see each role)" if en else "（见各角色）"
    no_extract = (
        "(could not extract shared defaults; each role listed in full)"
        if en else "（未能从角色参数中抽出共性，以下均按角色完整列出）"
    )

    by_obj: dict[str, list[tuple[str, dict]]] = {o: [] for o in _OBJ_ORDER}
    for rid, spec in (role_params or {}).items():
        if not isinstance(spec, dict):
            continue
        obj = _bucket(rid, spec)
        by_obj.setdefault(obj, []).append((rid, spec))

    title = "Format requirements (plain text)" if en else "格式要求（纯文本）"
    lines: list[str] = [
        f"# {title}" + (f" — {genre}" if genre else ""),
        "",
        "> Shared defaults first, then per-role exceptions." if en else
        "> 先写各类对象的**共性默认**，再写各角色相对默认的**例外**。",
        "> Role headings are natural-language names in this document's language; never style_id." if en else
        "> 角色用主题语言的自然语言称呼（如「三级标题」）；**不出现** style_id。",
        "> Props below are changes still needed on the paired init.docx (not a full style dump)." if en else
        "> 下列格式属性为相对配对 init.docx **仍需修改**的项（非目标样式全量罗列）。",
    ]
    if source_note:
        lines.append(f"> Source: {source_note}" if en else f"> 来源：{source_note}")
    lines.append("")

    lines.append("## Shared defaults" if en else "## 共性默认")
    lines.append("")
    defaults_by_obj: dict[str, dict[str, Any]] = {}
    for obj in _OBJ_ORDER:
        items = by_obj.get(obj) or []
        if not items or obj == "table":
            continue
        flats = [_flat_props(spec) for _, spec in items]
        # Skip empty-need roles when computing commons
        flats = [f for f in flats if f]
        defaults = _majority_defaults(flats) if flats else {}
        defaults_by_obj[obj] = defaults
        ot = obj_title.get(obj, obj)
        if not items:
            continue
        if not flats:
            lines.append(
                f"- **{ot}**: {no_change}" if en else f"- **{ot}**：{no_change}"
            )
        elif not defaults:
            lines.append(f"- **{ot}**: {no_common}" if en else f"- **{ot}**：{no_common}")
        else:
            body = describe_props(defaults, locale=loc) or see_roles
            lines.append(f"- **{ot}**: {body}" if en else f"- **{ot}**：{body}")
    if not any(defaults_by_obj.values()):
        lines.append(f"- {no_extract}")
    lines.append("")

    ordered_ids: list[str] = []
    seen: set[str] = set()
    for item in stylesheet or []:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("style_id") or item.get("role_id") or "")
        if sid and sid in (role_params or {}) and sid not in seen:
            ordered_ids.append(sid)
            seen.add(sid)
    for rid in (role_params or {}):
        if rid not in seen:
            ordered_ids.append(rid)

    for obj in _OBJ_ORDER:
        items = [
            (rid, role_params[rid]) for rid in ordered_ids
            if rid in role_params and _bucket(rid, role_params[rid] or {}) == obj
        ]
        if not items:
            continue
        lines.append(f"## {obj_title.get(obj, obj)} roles" if en else f"## {obj_title.get(obj, obj)}角色")
        lines.append("")
        defaults = defaults_by_obj.get(obj) or {}
        for rid, spec in items:
            name = public_display_name(
                rid, name_by.get(rid), spec.get("display_name"), locale=loc,
            )
            if not name:
                continue
            purpose = public_purpose(rid, desc_by.get(rid), spec.get("description"), locale=loc)
            if not purpose:
                purpose = (
                    "Used at the corresponding position in the document."
                    if en else "该样式在文中对应位置使用。"
                )
            lines.append(f"### {name}")
            lines.append(
                f"- **{purpose_k}**: {_san(purpose)}" if en else f"- **{purpose_k}**：{_san(purpose)}"
            )
            if obj == "table":
                tbl_lines = _describe_table(spec, pos_para.get(rid) or {}, name_by, locale=loc)
                # Empty need → no change
                props = spec.get("props") or {}
                tf = props.get("table_format") if isinstance(props.get("table_format"), dict) else {}
                cells = props.get("cells") if isinstance(props.get("cells"), dict) else {}
                if not tf and not cells and not any(
                    k for k in (spec.get("props") or {}) if k not in ("table_format", "cells")
                ):
                    lines.append(f"- **{fmt_k}**: {no_change}" if en else f"- **{fmt_k}**：{no_change}")
                else:
                    for tl in tbl_lines:
                        lines.append(f"- {tl}")
            elif obj == "section":
                flat = _flat_props(spec)
                delta = _diff_props(flat, defaults)
                chrome = _section_chrome_lines(spec, locale=loc)
                if delta:
                    body = describe_props(delta, locale=loc)
                    lines.append(f"- **{lay_ex}**: {body}" if en else f"- **{lay_ex}**：{body}")
                elif not defaults and flat:
                    body = describe_props(flat, locale=loc)
                    if body:
                        lines.append(f"- **{lay_k}**: {body}" if en else f"- **{lay_k}**：{body}")
                elif not flat and not chrome:
                    lines.append(f"- **{lay_k}**: {no_change}" if en else f"- **{lay_k}**：{no_change}")
                elif defaults and not delta:
                    lines.append(f"- **{lay_k}**: {same_lay}" if en else f"- **{lay_k}**：{same_lay}")
                for cl in chrome:
                    lines.append(f"- {cl}")
            else:
                flat = _flat_props(spec)
                delta = _diff_props(flat, defaults)
                if delta:
                    body = describe_props(delta, locale=loc)
                    lines.append(f"- **{fmt_ex}**: {body}" if en else f"- **{fmt_ex}**：{body}")
                elif not flat:
                    lines.append(f"- **{fmt_k}**: {no_change}" if en else f"- **{fmt_k}**：{no_change}")
                elif not defaults:
                    body = describe_props(flat, locale=loc) or inherit_body
                    lines.append(f"- **{fmt_k}**: {body}" if en else f"- **{fmt_k}**：{body}")
                else:
                    lines.append(f"- **{fmt_k}**: {same_fmt}" if en else f"- **{fmt_k}**：{same_fmt}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _is_en_locale(*, genre: str = "", locale: str = "") -> bool:
    loc = str(locale or "").lower()
    if loc.startswith("en"):
        return True
    g = str(genre or "").lower()
    return g.endswith("_en") or "_en_" in g


def build_t_style_purposes(
    *,
    genre: str = "",
    stylesheet: list | None = None,
    role_params: dict | None = None,
    locale: str = "",
    heading_ids: set[str] | None = None,
) -> str:
    """用途说明：display_name + description，按细桶分节。"""
    loc = locale or ("en" if _is_en_locale(genre=genre) else "zh")
    en = _is_en_locale(genre=genre, locale=loc)
    sep = ": " if en else "："
    empty = "(no description)" if en else "（无说明）"
    obj_title = _OBJ_TITLE_EN if en else _OBJ_TITLE

    if heading_ids is None:
        from LongDocFmtEval.pipeline_auto.seed.step7_format_diff import heading_style_ids  # noqa: PLC0415
        heading_ids = heading_style_ids(stylesheet, role_params)

    entries: list[dict] = [e for e in (stylesheet or []) if isinstance(e, dict)]
    if not entries and role_params:
        for rid, spec in role_params.items():
            if not isinstance(spec, dict):
                continue
            entries.append({
                "style_id": rid,
                "display_name": spec.get("display_name"),
                "description": spec.get("description"),
                "object": spec.get("object"),
            })

    buckets: dict[str, list[tuple[str, str, str]]] = {o: [] for o in _OBJ_ORDER}
    for item in entries:
        sid = str(item.get("style_id") or item.get("role_id") or "")
        dn = public_display_name(sid, item.get("display_name"), locale=loc)
        if not dn:
            continue
        desc = _san(item.get("description")) or public_purpose(sid, locale=loc) or empty
        bucket = object_bucket(item.get("object"), sid, heading_ids=heading_ids)
        if bucket not in buckets:
            continue
        buckets[bucket].append((sid, dn, desc))

    lines: list[str] = []
    for obj in _OBJ_ORDER:
        items = buckets.get(obj) or []
        if not items:
            continue
        heading = f"## {obj_title.get(obj, obj)} roles" if en else f"## {obj_title.get(obj, obj)}角色"
        lines.append(heading)
        lines.append("")
        for _sid, dn, desc in items:
            lines.append(f"- **{dn}**{sep}{desc}")
        lines.append("")
    return ("\n".join(lines).rstrip() + "\n") if lines else ""


def write_t_text_artifacts(
    out_dir: Path,
    role_params: dict,
    *,
    genre: str = "",
    lib: dict | None = None,
    stylesheet: list | None = None,
    locale: str = "",
    heading_ids: set[str] | None = None,
    source_note: str = "",
    text_filename: str = "t_text.md",
    meta_extra: dict[str, Any] | None = None,
) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    loc = locale or str((lib or {}).get("locale") or "")
    if not loc:
        loc = "en" if _is_en_locale(genre=genre) else "zh"
    if heading_ids is None:
        from LongDocFmtEval.pipeline_auto.seed.step7_format_diff import heading_style_ids  # noqa: PLC0415
        heading_ids = heading_style_ids(stylesheet, role_params)
    text = build_t_text(
        role_params,
        genre=genre,
        lib=lib,
        stylesheet=stylesheet,
        locale=loc,
        heading_ids=heading_ids,
        source_note=source_note,
    )
    purposes = build_t_style_purposes(
        genre=genre,
        stylesheet=stylesheet,
        role_params=role_params,
        locale=loc,
        heading_ids=heading_ids,
    )
    (out_dir / text_filename).write_text(text, encoding="utf-8")
    (out_dir / "t_style_propose.md").write_text(purposes, encoding="utf-8")
    leftover = out_dir / "t_style_purposes.md"
    if leftover.is_file():
        leftover.unlink()
    meta = {
        "genre": genre,
        "locale": loc,
        "role_count": len(role_params or {}),
        "stylesheet_count": len(stylesheet or []),
        "heading_style_count": len(heading_ids or []),
        "files": {"text": text_filename, "propose": "t_style_propose.md"},
        "purposes_fields": ["display_name", "description"],
        "purposes_exclude": ["style_id", "expectation"],
        "buckets": list(_OBJ_ORDER),
    }
    if meta_extra:
        meta.update(meta_extra)
    dump_json(out_dir / "t_text_meta.json", meta)
    if text_filename != "t_text.md":
        dump_json(out_dir / f"{Path(text_filename).stem}_meta.json", meta)
    return {
        "t_text": text_filename,
        "t_style_propose": "t_style_propose.md",
    }
