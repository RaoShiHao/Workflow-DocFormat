"""Step7 · compact text_input.md (single locale, paragraph-style per target)."""
from __future__ import annotations

from typing import Any

from LongDocFmtEval.pipeline_auto.seed.heading_contract import (
    parse_heading_level,
    parse_title_kind,
)
from LongDocFmtEval.pipeline_auto.seed.heading_numbering_spec import render_heading_level_note
from LongDocFmtEval.pipeline_auto.seed.step6_t_text import (
    _OBJ_ORDER,
    _OBJ_TITLE,
    _OBJ_TITLE_EN,
    _describe_table,
    _diff_props,
    _flat_props,
    _is_en_locale,
    _majority_defaults,
    _pos_para_from_lib,
    _role_meta,
    _san,
    _section_chrome_lines,
    describe_props,
    object_bucket,
    public_display_name,
    public_purpose,
    stylesheet_name_desc,
)
from LongDocFmtEval.pipeline_auto.seed.step7_format_diff import (
    build_need_role_params,
    heading_style_ids,
)
from LongDocFmtEval.pipeline_auto.seed.training_factors import (
    text_numbering_note,
    text_target_notes,
    uses_text_input,
)


def _join_parts(parts: list[str], *, en: bool) -> str:
    sep = "; " if en else "，"
    body = sep.join(p for p in parts if p and str(p).strip())
    return body.strip()


def _ensure_sentence_end(text: str, *, en: bool) -> str:
    """Ensure a format / note sentence ends with a terminal period."""
    s = str(text or "").strip()
    if not s:
        return s
    if en:
        if s[-1] in ".!?":
            return s
        return s + "."
    if s[-1] in "。！？.!?":
        return s
    return s + "。"


def _role_format_body(
    rid: str,
    spec: dict,
    *,
    obj: str,
    defaults: dict[str, Any],
    pos_para: dict[str, dict[str, str]],
    name_by: dict[str, str],
    locale: str,
    en: bool,
) -> str:
    no_change = (
        "no further changes needed (already matches target on this init)"
        if en
        else "无需再改（相对本 init 已与目标一致）"
    )
    inherit_body = "inherit body baseline" if en else "沿用正文基线"
    same_fmt = "same as shared defaults for this layer" if en else "同本节共性默认"
    same_lay = "same as shared section defaults" if en else "同本节共性默认"

    if obj == "table":
        tbl_lines = _describe_table(spec, pos_para.get(rid) or {}, name_by, locale=locale)
        props = spec.get("props") or {}
        tf = props.get("table_format") if isinstance(props.get("table_format"), dict) else {}
        cells = props.get("cells") if isinstance(props.get("cells"), dict) else {}
        if not tf and not cells and not any(
            k for k in (spec.get("props") or {}) if k not in ("table_format", "cells")
        ):
            return no_change
        return _join_parts(tbl_lines, en=en)

    if obj == "section":
        flat = _flat_props(spec)
        delta = _diff_props(flat, defaults)
        chrome = _section_chrome_lines(spec, locale=locale)
        chunks: list[str] = []
        if delta:
            chunks.append(describe_props(delta, locale=locale))
        elif not defaults and flat:
            body = describe_props(flat, locale=locale)
            if body:
                chunks.append(body)
        elif defaults and not delta and not chrome:
            return same_lay
        elif not flat and not chrome:
            return no_change
        for cl in chrome:
            chunks.append(cl.lstrip("- ").strip())
        return _join_parts(chunks, en=en)

    flat = _flat_props(spec)
    delta = _diff_props(flat, defaults)
    if delta:
        return describe_props(delta, locale=locale)
    if not flat:
        return no_change
    if not defaults:
        return describe_props(flat, locale=locale) or inherit_body
    return same_fmt


def _heading_level_for_role(
    rid: str,
    spec: dict,
    stylesheet: list | None,
) -> int | None:
    """Outline level for numbering notes only. Cover/subtitle title_kind → skip."""
    if parse_title_kind(spec.get("title_kind")):
        return None
    lvl = parse_heading_level(spec.get("heading_level"))
    if lvl is not None:
        return lvl
    for item in stylesheet or []:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("style_id") or item.get("role_id") or "")
        if sid != rid:
            continue
        if parse_title_kind(item.get("title_kind")):
            return None
        lvl = parse_heading_level(item.get("heading_level"))
        if lvl is not None:
            return lvl
    return None


def build_unified_targets_md(
    role_params: dict[str, Any],
    *,
    genre: str = "",
    lib: dict | None = None,
    stylesheet: list | None = None,
    locale: str = "",
    heading_ids: set[str] | None = None,
    include_format: bool = True,
    include_target_notes: bool = True,
    include_numbering_notes: bool = False,
    scheme_id: str | None = None,
    item_style: str = "heading",
) -> str:
    """Per-target prose.

    ``item_style``:
      - ``heading`` — ``### Name`` + paragraphs (text_requirement)
      - ``list`` — ``- **Name**: …`` under ``##`` buckets (template_w_text catalog)
    """
    loc = locale or str((lib or {}).get("locale") or "")
    if not loc:
        loc = "en" if _is_en_locale(genre=genre) else "zh"
    en = _is_en_locale(genre=genre, locale=loc)
    list_mode = str(item_style or "heading").strip().lower() == "list"
    name_by, desc_by = _role_meta(role_params, lib)
    _ss_name, _ss_desc, ss_obj = stylesheet_name_desc(stylesheet)
    name_by.update(_ss_name)
    desc_by.update(_ss_desc)
    pos_para = _pos_para_from_lib(lib)

    if heading_ids is None:
        heading_ids = heading_style_ids(stylesheet, role_params)

    def _bucket(rid: str, spec: dict) -> str:
        return object_bucket(
            spec.get("object") or ss_obj.get(rid), rid, heading_ids=heading_ids,
        ) or "?"

    obj_title = _OBJ_TITLE_EN if en else _OBJ_TITLE
    fallback_purpose = (
        "Used at the corresponding position in the document."
        if en else "该样式在文中对应位置使用。"
    )

    by_obj: dict[str, list[tuple[str, dict]]] = {o: [] for o in _OBJ_ORDER}
    for rid, spec in (role_params or {}).items():
        if not isinstance(spec, dict):
            continue
        by_obj.setdefault(_bucket(rid, spec), []).append((rid, spec))

    defaults_by_obj: dict[str, dict[str, Any]] = {}
    for obj in _OBJ_ORDER:
        items = by_obj.get(obj) or []
        if not items or obj == "table":
            continue
        flats = [_flat_props(spec) for _, spec in items]
        flats = [f for f in flats if f]
        defaults_by_obj[obj] = _majority_defaults(flats) if flats else {}

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

    lines: list[str] = []
    for obj in _OBJ_ORDER:
        items = [
            (rid, role_params[rid]) for rid in ordered_ids
            if rid in role_params and _bucket(rid, role_params[rid] or {}) == obj
        ]
        if not items:
            continue
        section_lines: list[str] = []
        defaults = defaults_by_obj.get(obj) or {}
        for rid, spec in items:
            name = public_display_name(
                rid, name_by.get(rid), spec.get("display_name"), locale=loc,
            )
            if not name:
                continue
            chunks: list[str] = []
            if include_target_notes:
                purpose = public_purpose(
                    rid, desc_by.get(rid), spec.get("description"), locale=loc,
                ) or fallback_purpose
                chunks.append(_san(purpose).rstrip("。.!?！？"))
            if include_format and not list_mode:
                fmt_body = _role_format_body(
                    rid, spec, obj=obj, defaults=defaults,
                    pos_para=pos_para, name_by=name_by, locale=loc, en=en,
                )
                if fmt_body:
                    chunks.append(fmt_body)
            if include_numbering_notes and scheme_id and obj == "paragraph_heading" and rid in (heading_ids or set()):
                lvl = _heading_level_for_role(rid, spec, stylesheet)
                if lvl is not None:
                    note = render_heading_level_note(
                        scheme_id, lvl, display_name=name, locale=loc,
                    ).strip()
                    if note:
                        chunks.append(note.rstrip("。.!?！？") if list_mode else note)
            if not chunks:
                continue
            if list_mode:
                # One bullet: purpose (+ optional numbering) as continuous prose.
                body_parts = [str(c).strip().rstrip("。.!?！？") for c in chunks if str(c).strip()]
                joined = ". ".join(body_parts) if en else "。".join(body_parts)
                section_lines.append(
                    f"- **{name}**: {_ensure_sentence_end(joined, en=en)}"
                )
            else:
                section_lines.append(f"### {name}")
                section_lines.append("")
                for i, c in enumerate(chunks):
                    text = str(c).strip()
                    # format / purpose get a terminal period; numbering note already has one
                    is_num = include_numbering_notes and i == len(chunks) - 1 and (
                        "may look like" in text or "可形如" in text
                    )
                    if not is_num:
                        text = _ensure_sentence_end(text, en=en)
                    section_lines.append(text)
                section_lines.append("")

        if not section_lines:
            continue
        lines.append(f"## {obj_title.get(obj, obj)}")
        lines.append("")
        lines.extend(section_lines)
        if list_mode:
            lines.append("")

    return "\n".join(lines).strip()


def build_text_input_md(
    *,
    role_params: dict[str, Any],
    genre: str,
    lib: dict | None,
    stylesheet: list | None,
    heading_ids: set[str],
    init_docx,
    golden,
    diff_meta: dict[str, Any],
    factors: dict[str, Any],
    locale_primary: str,
    map_json: dict[str, Any],
    styles_json: dict[str, Any],
    perturb_plan: dict[str, Any] | None = None,
) -> str:
    """Single-locale text_input: text_requirement prose / template_w_text notes+delta."""
    del diff_meta
    loc = str(locale_primary or factors.get("locale") or "zh").lower()
    if loc.startswith("en"):
        loc = "en"
    else:
        loc = "zh"
    en = loc == "en"

    if not uses_text_input(factors):
        return ""

    mode = str(factors.get("input_mode") or "")
    hybrid = factors.get("hybrid_text") if isinstance(factors.get("hybrid_text"), dict) else {}
    include_catalog = bool(hybrid.get("include_catalog")) if hybrid else mode == "template_w_text"
    include_delta = bool(hybrid.get("include_delta")) if hybrid else False
    # Format property diffs only in text_requirement; template_w_text delta is separate.
    include_format = mode == "text_requirement"

    show_target_notes = text_target_notes(factors)
    show_numbering = text_numbering_note(factors)
    scheme = factors.get("init", {}).get("numbering", {}).get("scheme_id")
    if not scheme:
        from LongDocFmtEval.pipeline_auto.seed.training_factors import scheme_id as _scheme_id  # noqa: PLC0415
        scheme = _scheme_id(factors)

    parts: list[str] = []

    if mode == "template_w_text":
        has_catalog = include_catalog and (show_target_notes or show_numbering)
        has_delta = bool(include_delta and perturb_plan and perturb_plan.get("ok"))

        if has_catalog:
            parts.append("# Formatting Target notes" if en else "# 格式目标补充说明")
            parts.append("")
            parts.append(
                "The following supplements `template.docx` and can help you interpret "
                "the formatting targets in the template."
                if en
                else "以下为对 template.docx 中格式目标的补充说明，可辅助理解模板中各样式的用途。"
            )
            parts.append("")
            catalog = build_unified_targets_md(
                role_params,
                genre=genre,
                lib=lib,
                stylesheet=stylesheet,
                locale=loc,
                heading_ids=heading_ids,
                include_format=False,
                include_target_notes=show_target_notes,
                include_numbering_notes=show_numbering,
                scheme_id=str(scheme) if scheme else None,
                item_style="list",
            ).strip()
            if catalog:
                parts.append(catalog)
                parts.append("")

        if has_delta:
            from LongDocFmtEval.pipeline_auto.seed.template_perturb import (  # noqa: PLC0415
                render_delta_md,
            )

            delta = render_delta_md(
                perturb_plan,
                locale=loc,
                as_section=has_catalog,
            ).strip()
            if delta:
                parts.append(delta)
                parts.append("")

        return "\n".join(parts).rstrip() + "\n"

    # --- text_requirement ---
    main_title = "Format adjustment requirements" if en else "格式调整需求"
    parts.append(f"# {main_title}")
    parts.append("")

    need, _meta = build_need_role_params(
        role_params,
        golden=golden,
        init=init_docx if init_docx and getattr(init_docx, "is_file", lambda: False)() else None,
        styles=styles_json or {},
        maps=map_json or {},
        locale=loc,
        stylesheet=stylesheet,
    )
    params_source = need or role_params
    for rid, spec in (role_params or {}).items():
        if rid not in need or not isinstance(need.get(rid), dict):
            continue
        if not isinstance(spec, dict):
            continue
        for key in ("display_name", "description", "object", "example"):
            if need[rid].get(key) in (None, "") and spec.get(key):
                need[rid][key] = spec[key]

    body = build_unified_targets_md(
        params_source,
        genre=genre,
        lib=lib,
        stylesheet=stylesheet,
        locale=loc,
        heading_ids=heading_ids,
        include_format=include_format,
        include_target_notes=show_target_notes,
        include_numbering_notes=show_numbering,
        scheme_id=str(scheme) if scheme else None,
        item_style="heading",
    ).strip()
    if body:
        parts.append(body)
        parts.append("")

    return "\n".join(parts).rstrip() + "\n"
