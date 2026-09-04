from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from LongDocFormatter.workflow.cell_plan import index_cell_para_styles, infer_table_style
from LongDocFormatter.workflow.contracts import Catalog, CatalogEntry, DocElement, Layer
from LongDocFormatter.workflow.json_util import parse_llm_json
from LongDocFormatter.workflow.officecli_doc import get_format, table_format_of
from LongDocFormatter.workflow.whitelist import (
    CELL_KEYS,
    HEADER_FOOTER_KEYS,
    dump_whitelist_for_prompt,
    filter_props,
    merge_element_props,
    whitelist_keys,
)

STEP_TARGET_ATTRIBUTE_SPEC = "04_target_attribute_spec"
STEP_TARGET_SPEC = STEP_TARGET_ATTRIBUTE_SPEC

_DELTA_SECTION_RE = re.compile(
    r"(?:^|\n)\s*#{1,3}\s*(?:Adjustments relative to the template|相对模板的格式调整)\s*\n",
    re.I | re.M,
)


def bind_catalog_to_template(
    catalog: Catalog,
    template_elements: Dict[Layer, List[DocElement]],
    *,
    language_model,
    prompts: Dict[str, Dict[str, str]],
    llm_kwargs: Dict[str, Any],
    skip_if_single: bool = True,
) -> Catalog:
    """Classify template instances into an existing catalog and set exemplars.

    Not used by the default pipeline (``build_target_set`` / from_template op clusters the
    template first). Kept for experiments that start from a text-defined catalog.
    """
    from LongDocFormatter.workflow.assignment import assign_layer

    for layer, elements in (template_elements or {}).items():
        entries = catalog.by_layer(layer)
        if not entries or not elements:
            continue
        prompt = prompts.get(layer) or prompts.get("paragraph.body")
        if not prompt:
            continue
        mapping = assign_layer(
            layer=layer,
            catalog=catalog,
            elements=elements,
            language_model=language_model,
            prompt=prompt,
            llm_kwargs=llm_kwargs,
            batch_size=80,
            skip_if_single=skip_if_single,
            use_vision=False,
        )
        loc_to_el = {str(e.location_id): e for e in elements}
        filled: set[str] = set()
        for loc, sid in mapping.items():
            if sid in filled:
                continue
            el = loc_to_el.get(str(loc))
            if not el:
                continue
            for entry in catalog.entries:
                if entry.style_id == sid:
                    entry.exemplar_path = el.path
                    entry.exemplar_location_id = el.location_id
                    filled.add(sid)
                    break
    return catalog


def declarations_from_exemplars(
    catalog: Catalog,
    *,
    layer_elements: Dict[Layer, List[DocElement]],
    already: Dict[str, Dict[str, Any]] | None = None,
) -> Dict[str, Dict[str, Any]]:
    """Copy whitelist props from catalog exemplar (template path; no extra LLM)."""
    out = dict(already or {})
    by_loc: Dict[tuple[str, str], DocElement] = {}
    for layer, els in layer_elements.items():
        for el in els:
            by_loc[(layer, str(el.location_id))] = el
            by_loc[(layer, el.path)] = el

    pending_tables: list[tuple[CatalogEntry, DocElement]] = []
    for entry in catalog.entries:
        if entry.style_id in out and out[entry.style_id]:
            continue
        el = None
        if entry.exemplar_path:
            el = by_loc.get((entry.object, entry.exemplar_path))
        if el is None and entry.exemplar_location_id is not None:
            el = by_loc.get((entry.object, str(entry.exemplar_location_id)))
        if el is None:
            continue
        if entry.object == "table":
            pending_tables.append((entry, el))
            continue
        out[entry.style_id] = _declaration_payload(entry, el)
    cell_para_sids = index_cell_para_styles(layer_elements.get("paragraph.table_cell") or [], out)
    for entry, el in pending_tables:
        out[entry.style_id] = _declaration_payload(entry, el, cell_para_sids=cell_para_sids)
    return out


def _declaration_payload(
    entry: CatalogEntry,
    el: DocElement,
    *,
    cell_para_sids: Dict[str, str] | None = None,
) -> Dict[str, Any]:
    if entry.object == "table":
        designed = infer_table_style(el.to_dict(), cell_para_sids=cell_para_sids)
        payload: Dict[str, Any] = {
            "object": "table",
            "table_format": table_format_of(el.props),
            "cells": designed.get("cells") or {},
            "cell_style_plan": designed.get("cell_style_plan") or {},
        }
        if designed.get("cell_paragraphs"):
            payload["cell_paragraphs"] = designed["cell_paragraphs"]
        return payload
    if entry.object == "section":
        payload = {"object": "section", "props": filter_props(el.props, whitelist_keys("section"))}
        hf = (el.meta or {}).get("header_footer") or el.props.get("_header_footer")
        if isinstance(hf, dict):
            for k in ("header", "footer", "header_first"):
                if hf.get(k):
                    payload[k] = hf[k]
        return payload
    return {"object": entry.object, "props": dict(el.props)}


def refresh_from_paths(catalog: Catalog, doc_path, declarations: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Re-get exemplar paths so declarations match live template."""
    out = dict(declarations)
    from pathlib import Path

    doc = Path(doc_path)
    for entry in catalog.entries:
        if not entry.exemplar_path:
            continue
        fmt = merge_element_props(get_format(doc, entry.exemplar_path))
        keys = whitelist_keys(entry.object)
        if entry.object == "table":
            continue
        out[entry.style_id] = {"object": entry.object, "props": filter_props(fmt, keys)}
    return out


def extract_requirement_delta(text: str) -> str:
    """Prefer the explicit delta section; otherwise use full requirement text."""
    raw = str(text or "").strip()
    if not raw:
        return ""
    match = _DELTA_SECTION_RE.search(raw)
    if not match:
        return raw
    start = match.end()
    tail = raw[start:]
    next_heading = re.search(r"\n\s*#{1,3}\s+\S", tail)
    if next_heading:
        tail = tail[: next_heading.start()]
    return tail.strip() or raw


def summarize_base_for_prompt(
    base: Dict[str, Dict[str, Any]],
    entries: List[CatalogEntry],
    *,
    max_props: int = 8,
) -> str:
    """Compact per-role baseline for patch prompts (read-only context)."""
    lines: List[str] = []
    for entry in entries:
        spec = base.get(entry.style_id) or {}
        if not spec:
            lines.append(f"- {entry.style_id} ({entry.display_name}): (no exemplar props)")
            continue
        obj = str(spec.get("object") or entry.object)
        if obj == "table":
            tf = spec.get("table_format") or {}
            cells = spec.get("cells") or {}
            slots = sorted(cells.keys())[:6]
            lines.append(
                f"- {entry.style_id}: table_format keys={sorted(tf.keys())[:max_props]}; "
                f"cells slots={slots}"
            )
            continue
        if obj == "section":
            props = spec.get("props") or {}
            props_preview = {k: props[k] for k in sorted(props.keys())[:max_props]}
            hf = [k for k in ("header", "footer", "header_first") if spec.get(k)]
            lines.append(
                f"- {entry.style_id}: props={json.dumps(props_preview, ensure_ascii=False)}; "
                f"nested={hf}"
            )
            continue
        props = spec.get("props") if isinstance(spec.get("props"), dict) else spec
        props_preview = {k: props[k] for k in sorted((props or {}).keys())[:max_props]}
        lines.append(
            f"- {entry.style_id} ({entry.display_name}): "
            f"{json.dumps(props_preview, ensure_ascii=False)}"
        )
    return "\n".join(lines) if lines else "(empty baseline)"


def declarations_from_text(
    *,
    catalog: Catalog,
    text: str,
    language_model,
    prompt: Dict[str, str],
    llm_kwargs: Dict[str, Any],
    locale: str = "en",
    warnings: List[Dict[str, Any]] | None = None,
    coverage: str = "full",
    base_for_context: Dict[str, Dict[str, Any]] | None = None,
) -> Dict[str, Dict[str, Any]]:
    """Build target attributes (A) from requirement text — one LLM batch per object layer.

    ``catalog`` is the target set T (``target_set.json``). Output is ``target_attributes``.
    ``coverage=sparse`` uses the patch prompt and only the delta section of ``text``;
    ``base_for_context`` is read-only prompt context (merge is done by ``overlay`` op).
    """
    sparse = str(coverage).lower() == "sparse"
    source_text = extract_requirement_delta(text) if sparse else text
    if sparse and not source_text.strip():
        return {}
    root_key = "patches" if sparse else "declarations"
    warn = warnings if warnings is not None else []
    by_layer: Dict[str, List[CatalogEntry]] = {}
    for e in catalog.entries:
        by_layer.setdefault(e.object, []).append(e)

    out: Dict[str, Dict[str, Any]] = {}
    for layer, entries in by_layer.items():
        allowed_ids = {e.style_id for e in entries}
        roles = [
            {
                "style_id": e.style_id,
                "display_name": e.display_name,
                "description": e.description,
                "object": e.object,
            }
            for e in entries
        ]
        user = prompt["user_template"]
        user = user.replace("{{element_type}}", layer)
        user = user.replace("{{whitelist}}", dump_whitelist_for_prompt(layer, locale=locale))
        user = user.replace("{{roles_json}}", json.dumps(roles, ensure_ascii=False, indent=2))
        if sparse:
            user = user.replace(
                "{{base_summary}}",
                summarize_base_for_prompt(base_for_context or {}, entries),
            )
        user = user.replace("{{text_input}}", source_text[:18000 if not sparse else 12000])
        parsed = _chat_declarations(
            language_model,
            prompt["system"],
            user,
            llm_kwargs,
            warnings=warn,
            step=STEP_TARGET_SPEC,
            layer=layer,
            note="sparse" if sparse else "",
        )
        layer_out = _items_to_decls(layer, parsed, root_key=root_key)
        if layer == "table" and not sparse and _table_cells_empty(layer_out):
            retry_user = (
                user
                + "\n\nERROR: every table target had empty cells. "
                "Fill cells.header / cells.data (and label/value/stub/note if used) "
                "with fill and four-side borders (or border.all). Do not leave cells as {}."
            )
            parsed = _chat_declarations(
                language_model,
                prompt["system"],
                retry_user,
                llm_kwargs,
                warnings=warn,
                step=STEP_TARGET_SPEC,
                layer=layer,
                note="table_cells_empty_retry",
            )
            layer_out = _items_to_decls(layer, parsed)
        if layer == "section" and not sparse and _section_hf_empty(layer_out):
            retry_user = (
                user
                + "\n\nERROR: every section target lacked header/footer objects. "
                "Add nested header and/or footer (and header_first when titlePage) with "
                "text or field=page plus align/size/color. Do not put them inside props."
            )
            parsed = _chat_declarations(
                language_model,
                prompt["system"],
                retry_user,
                llm_kwargs,
                warnings=warn,
                step=STEP_TARGET_SPEC,
                layer=layer,
                note="section_hf_empty_retry",
            )
            layer_out = _items_to_decls(layer, parsed)
        filtered, ignored = _filter_layer_target_attributes(layer_out, allowed_ids)
        if ignored:
            warn.append(
                {
                    "step": STEP_TARGET_SPEC,
                    "layer": layer,
                    "kind": "cross_layer_style_ids_ignored",
                    "style_ids": ignored,
                    "message": "LLM returned style_ids not in this layer's target_set; ignored.",
                }
            )
        _merge_layer_target_attributes(out, filtered, layer=layer, warnings=warn)
    return out


def _filter_layer_target_attributes(
    layer_out: Dict[str, Dict[str, Any]],
    allowed_ids: set[str],
) -> tuple[Dict[str, Dict[str, Any]], List[str]]:
    kept: Dict[str, Dict[str, Any]] = {}
    ignored: List[str] = []
    for sid, spec in layer_out.items():
        if sid in allowed_ids:
            kept[sid] = spec
        else:
            ignored.append(sid)
    return kept, sorted(ignored)


def _merge_layer_target_attributes(
    out: Dict[str, Dict[str, Any]],
    layer_out: Dict[str, Dict[str, Any]],
    *,
    layer: str,
    warnings: List[Dict[str, Any]] | None = None,
) -> None:
    """Merge one layer's attributes into A without clobbering another layer's object type."""
    for sid, spec in layer_out.items():
        if sid not in out:
            out[sid] = spec
            continue
        prev = out[sid]
        prev_obj = str(prev.get("object") or "")
        new_obj = str(spec.get("object") or layer)
        if prev_obj and new_obj and prev_obj != new_obj:
            if warnings is not None:
                warnings.append(
                    {
                        "step": STEP_TARGET_SPEC,
                        "layer": layer,
                        "kind": "object_type_overwrite_blocked",
                        "style_id": sid,
                        "existing_object": prev_obj,
                        "incoming_object": new_obj,
                        "message": "Kept existing target_attributes object; skipped conflicting layer merge.",
                    }
                )
            continue
        out[sid] = spec


def _chat_declarations(
    language_model,
    system: str,
    user: str,
    llm_kwargs: Dict[str, Any],
    *,
    warnings: List[Dict[str, Any]] | None = None,
    step: str = STEP_TARGET_SPEC,
    layer: str = "",
    note: str = "",
) -> Dict[str, Any]:
    parsed = _parse_declarations_response(
        language_model,
        system,
        user,
        llm_kwargs,
        warnings=warnings,
        step=step,
        layer=layer,
        attempt=1,
        note=note,
    )
    if parsed:
        return parsed
    parsed = _parse_declarations_response(
        language_model,
        system,
        user,
        llm_kwargs,
        warnings=warnings,
        step=step,
        layer=layer,
        attempt=2,
        note=note or "parse_retry",
    )
    if warnings is not None:
        warnings.append(
            {
                "step": step,
                "layer": layer,
                "kind": "json_parse_failed",
                "note": note,
                "message": "Structured output could not be parsed after 2 attempts; skipped this layer batch.",
            }
        )
    return {}


def _parse_declarations_response(
    language_model,
    system: str,
    user: str,
    llm_kwargs: Dict[str, Any],
    *,
    warnings: List[Dict[str, Any]] | None,
    step: str,
    layer: str,
    attempt: int,
    note: str,
) -> Dict[str, Any]:
    try:
        result = language_model.chat_json(system=system, user=user, **llm_kwargs)
    except Exception as exc:
        if warnings is not None and attempt == 2:
            warnings.append(
                {
                    "step": step,
                    "layer": layer,
                    "kind": "llm_call_failed",
                    "attempt": attempt,
                    "note": note,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        return {}
    parsed = parse_llm_json(result)
    if isinstance(parsed, dict) and parsed:
        if warnings is not None and attempt == 2:
            warnings.append(
                {
                    "step": step,
                    "layer": layer,
                    "kind": "json_parse_retry_ok",
                    "note": note,
                    "message": "Second attempt parsed structured output successfully.",
                }
            )
        return parsed
    if warnings is not None and attempt == 1:
        warnings.append(
            {
                "step": step,
                "layer": layer,
                "kind": "json_parse_retry",
                "note": note,
                "message": "First structured output parse failed; retrying once.",
            }
        )
    return {}


def _items_to_decls(
    layer: str,
    parsed: Dict[str, Any],
    *,
    root_key: str = "declarations",
) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    if isinstance(parsed.get("by_style_id"), dict):
        for sid, spec in parsed["by_style_id"].items():
            if isinstance(spec, dict):
                out[str(sid)] = _normalize_decl(layer, spec)
        return out
    items = (
        parsed.get(root_key)
        or parsed.get("declarations")
        or parsed.get("styles")
        or parsed.get("roles")
        or parsed.get("patches")
        or []
    )
    if not isinstance(items, list):
        return out
    for it in items:
        if not isinstance(it, dict):
            continue
        sid = str(it.get("style_id") or "")
        if not sid:
            continue
        out[sid] = _normalize_decl(layer, it)
    return out


def _table_cells_empty(decls: Dict[str, Dict[str, Any]]) -> bool:
    tables = [spec for spec in decls.values() if spec.get("object") == "table"]
    if not tables:
        return False
    return all(not (spec.get("cells") or {}) for spec in tables)


def _normalize_hf(chrome: Dict[str, Any] | None) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in (chrome or {}).items():
        if k not in HEADER_FOOTER_KEYS:
            continue
        if v is None or v == "none":
            continue
        if v == "" and k != "text":
            continue
        out[k] = v
    return out


def _section_hf_empty(decls: Dict[str, Dict[str, Any]]) -> bool:
    sections = [spec for spec in decls.values() if spec.get("object") == "section"]
    if not sections:
        return False
    return all(
        not _normalize_hf(spec.get("header") if isinstance(spec.get("header"), dict) else None)
        and not _normalize_hf(spec.get("footer") if isinstance(spec.get("footer"), dict) else None)
        and not _normalize_hf(
            spec.get("header_first") if isinstance(spec.get("header_first"), dict) else None
        )
        for spec in sections
    )


def _normalize_cell_chrome(chrome: Dict[str, Any] | None) -> Dict[str, Any]:
    return {
        k: v
        for k, v in (chrome or {}).items()
        if k in CELL_KEYS and v not in (None, "")
    }


def _default_plan_from_slots(slots: set[str]) -> Dict[str, Any]:
    names = {str(s) for s in slots if s}
    if "label" in names or "value" in names:
        return {
            "mode": "label_value",
            "header_row": "header" in names,
            "column_styles": [
                "label" if "label" in names else "stub",
                "value" if "value" in names else "data",
            ],
        }
    if "stub" in names:
        return {"mode": "column", "header_row": "header" in names, "column_styles": ["stub", "data"]}
    if "header" in names:
        return {"mode": "row", "header_row": True, "row_styles": ["header", "data"]}
    if names:
        return {"mode": "row", "header_row": False, "row_styles": ["data"]}
    return {}


def _normalize_table_cells(raw: Any) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    if isinstance(raw, dict):
        items = raw.items()
    elif isinstance(raw, list):
        pairs: list[tuple[str, Dict[str, Any]]] = []
        for it in raw:
            if not isinstance(it, dict):
                continue
            name = str(it.get("slot") or it.get("cell_style") or it.get("name") or "").strip()
            chrome = {k: v for k, v in it.items() if k in CELL_KEYS}
            if name:
                pairs.append((name, chrome))
        items = pairs
    else:
        return out
    for name, chrome in items:
        if not name:
            continue
        if re.fullmatch(r"r\d+_c\d+", str(name), flags=re.I):
            continue
        filtered = _normalize_cell_chrome(chrome if isinstance(chrome, dict) else {})
        if filtered:
            out[str(name)] = filtered
    return out


def _normalize_decl(layer: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    if layer == "table":
        payload = {
            "object": "table",
            "table_format": spec.get("table_format") or filter_props(spec.get("props") or spec, whitelist_keys("table")),
            "cells": _normalize_table_cells(spec.get("cells")),
        }
        plan = spec.get("cell_style_plan") if isinstance(spec.get("cell_style_plan"), dict) else {}
        if not plan:
            plan = _default_plan_from_slots(set(payload["cells"]))
        if plan:
            payload["cell_style_plan"] = plan
        paras = spec.get("cell_paragraphs") if isinstance(spec.get("cell_paragraphs"), dict) else {}
        if paras:
            payload["cell_paragraphs"] = {str(k): str(v) for k, v in paras.items() if v}
        return payload
    if layer == "section":
        raw = spec.get("props") if isinstance(spec.get("props"), dict) else spec
        payload: Dict[str, Any] = {
            "object": "section",
            "props": filter_props(raw, whitelist_keys("section")),
        }
        for k in ("header", "footer", "header_first"):
            bag = spec.get(k)
            if not isinstance(bag, dict) and isinstance(raw, dict):
                bag = raw.get(k)
            if isinstance(bag, dict):
                cleaned = _normalize_hf(bag)
                if cleaned:
                    payload[k] = cleaned
        return payload
    props = spec.get("props") if isinstance(spec.get("props"), dict) else {
        k: v for k, v in spec.items() if k not in {"style_id", "object", "display_name", "description"}
    }
    return {"object": layer, "props": filter_props(props, whitelist_keys(layer))}


from LongDocFormatter.workflow.attribute_overlay import merge_attribute_dicts  # noqa: E402


def declarations_miss_template_outline(
    declarations: Dict[str, Dict[str, Any]] | None,
    template_elements: Dict[str, List[DocElement]] | None,
) -> bool:
    """True when template inventory has style-inherited keys declarations dropped."""
    from LongDocFormatter.officecli.read._ooxml_outline import coerce_outline_lvl

    keys = ("outlineLvl", "keepNext", "pageBreakBefore")
    paras = (template_elements or {}).get("paragraph.body") or []
    tmpl: dict[str, bool] = {k: False for k in keys}
    for el in paras:
        props = el.props or {}
        if coerce_outline_lvl(props.get("outlineLvl")) is not None:
            tmpl["outlineLvl"] = True
        if props.get("keepNext") not in (None, "", "none"):
            tmpl["keepNext"] = True
        if props.get("pageBreakBefore") not in (None, "", "none"):
            tmpl["pageBreakBefore"] = True
    if not any(tmpl.values()):
        return False
    seen = {k: False for k in keys}
    for spec in (declarations or {}).values():
        if not isinstance(spec, dict):
            continue
        props = spec.get("props") if isinstance(spec.get("props"), dict) else spec
        props = props or {}
        if coerce_outline_lvl(props.get("outlineLvl")) is not None:
            seen["outlineLvl"] = True
        if props.get("keepNext") not in (None, "", "none"):
            seen["keepNext"] = True
        if props.get("pageBreakBefore") not in (None, "", "none"):
            seen["pageBreakBefore"] = True
    return any(tmpl[k] and not seen[k] for k in keys)


def validate_declarations(declarations: Dict[str, Dict[str, Any]]) -> List[str]:
    notes: List[str] = []
    seen: Dict[str, str] = {}
    for sid, spec in declarations.items():
        obj = str(spec.get("object") or "")
        if obj == "table":
            blob = json.dumps({"tf": spec.get("table_format"), "c": spec.get("cells")}, sort_keys=True)
        else:
            blob = json.dumps(spec.get("props") or {}, sort_keys=True, default=str)
        if blob in seen:
            notes.append(f"collision: {sid} ~ {seen[blob]}")
        else:
            seen[blob] = sid
    return notes
