"""Assign init instances to catalog styles.

Units (one LLM call per object layer unless batch_sizes sets an int):
- section: whole layer
- paragraph.body + run: all paragraphs (style + inline fragments together)
- table: all tables (hop 1 structure + hop 2 cell_style; fonts from cell_paragraphs[slot])
- image: all pictures + nearby body paragraphs

``build_assignment_flat`` is the non-layered ablation: one joint LLM call for all
layers (see AblationStudy/NoLayeredLoc).
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, Iterable, List

from LongDocFmt.workflow.catalog import header_row, pick_caption
from LongDocFmt.workflow.cell_plan import coerce_slot, designed_slots
from LongDocFmt.workflow.contracts import Assignment, Catalog, CatalogEntry, DocElement, Layer
from LongDocFmt.workflow.json_util import (
    LlmJsonParseError,
    compact_json_dumps,
    is_scalar_map,
    parse_llm_json_strict,
)
from LongDocFmt.workflow.llm_trace import LlmBudgetExceeded, budget_exhausted, logger_of
from LongDocFmt.workflow.run_span import inventory_span_cues, normalize_run_span, paragraph_payload_content


def _has_visible_text(el: DocElement) -> bool:
    """Body paragraphs with no visible text are placeholders (images, spacers)."""
    return bool(str(el.content or "").strip())


def _user_prompt(prompt: Dict[str, str]) -> str:
    return str(prompt.get("user_template") or "")


def _catalog_brief(entries: List[CatalogEntry]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for e in entries:
        row: Dict[str, Any] = {
            "style_id": e.style_id,
            "display_name": e.display_name,
            "description": e.description,
        }
        if e.caption_type:
            row["caption_type"] = e.caption_type
        if e.header_semantics:
            row["header_semantics"] = e.header_semantics
        if e.captions:
            row["captions"] = list(e.captions)
        if e.header_rows:
            row["header_rows"] = list(e.header_rows)
        if e.typical_sections:
            row["typical_sections"] = list(e.typical_sections)
        rows.append(row)
    return rows


def _name_to_id(entries: List[CatalogEntry]) -> Dict[str, str]:
    out = {e.display_name: e.style_id for e in entries}
    out.update({e.style_id: e.style_id for e in entries})
    return out


def _resolve_style(name: Any, lookup: Dict[str, str]) -> str | None:
    style_name = str(name or "").strip()
    if not style_name or style_name.lower() == "other":
        return None
    return lookup.get(style_name)


def _require_json_list(result: Any, *keys: str, layer: str) -> List[Any]:
    parsed = parse_llm_json_strict(result, layer=layer, list_keys=keys)
    for key in keys:
        items = parsed.get(key)
        if isinstance(items, list):
            return items
    raise LlmJsonParseError(
        f"{layer}: JSON missing one of {keys}",
        layer=layer,
        raw=str(parsed)[:800],
    )


def _record_parse_failure(language_model: Any, err: LlmJsonParseError) -> None:
    log = logger_of(language_model)
    if log is None:
        return
    log.note_parse_failure(layer=err.layer, message=str(err), raw=err.raw)


def _parsed_list_or_empty(
    result: Any,
    *keys: str,
    layer: str,
    language_model: Any,
) -> List[Any]:
    """Parse this call's JSON list. On failure, record and return [] so later calls still run."""
    try:
        return _require_json_list(result, *keys, layer=layer)
    except LlmJsonParseError as err:
        _record_parse_failure(language_model, err)
        return []


def _batch_size(batch_sizes: Dict[str, Any], key: str, n_items: int, default: int | None = None) -> int:
    if n_items <= 0:
        return 1
    raw = batch_sizes.get(key, default)
    if raw in (None, "", "all", 0, "null"):
        return n_items
    return max(1, int(raw))


def _trim_neighbors(
    el: DocElement,
    n: int,
    body_paragraphs: List[DocElement] | None = None,
) -> tuple[list, list]:
    if n <= 0:
        return [], []
    meta = el.meta or {}
    before = _nonempty_previews(meta.get("neighbor_before") or [], n, tail=True)
    after = _nonempty_previews(meta.get("neighbor_after") or [], n, tail=False)
    if before or after or not body_paragraphs:
        return before, after
    host = str(meta.get("para_path") or "")
    if not host:
        return [], []
    try:
        hi = next(i for i, p in enumerate(body_paragraphs) if p.path == host)
    except StopIteration:
        return [], []
    vis = [p for p in body_paragraphs if _has_visible_text(p)]
    try:
        vi = next(i for i, p in enumerate(vis) if p.location_id == body_paragraphs[hi].location_id)
    except StopIteration:
        return [], []
    before = [
        {"location_id": p.location_id, "content": (p.content or "")[:180]}
        for p in vis[max(0, vi - n) : vi]
    ]
    after = [
        {"location_id": p.location_id, "content": (p.content or "")[:180]}
        for p in vis[vi + 1 : vi + 1 + n]
    ]
    return before, after


def _nonempty_previews(rows: Any, n: int, *, tail: bool) -> list:
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        text = str(row.get("content") or "").strip()
        if not text:
            continue
        out.append({"location_id": row.get("location_id"), "content": text[:180]})
    if n <= 0:
        return []
    return out[-n:] if tail else out[:n]


def _cue_value(el: DocElement, key: str) -> Any:
    props = el.props or {}
    meta = el.meta or {}
    if key in props and props.get(key) not in (None, "", "none"):
        return props.get(key)
    if key == "styleName":
        for alt in ("styleName", "style", "styleId"):
            if props.get(alt) not in (None, "", "none"):
                return props.get(alt)
    if key in meta and meta.get(key) not in (None, "", "none"):
        return meta.get(key)
    return None


def contrastive_cues(
    elements: List[DocElement],
    keys: Iterable[str] | None,
    *,
    mode: str = "contrastive",
) -> List[Dict[str, Any]]:
    key_list = [str(k) for k in (keys or []) if str(k).strip()]
    if not key_list:
        return [{} for _ in elements]
    bags: List[Dict[str, Any]] = []
    for el in elements:
        bag = {}
        for key in key_list:
            val = _cue_value(el, key)
            if val not in (None, "", "none"):
                bag[key] = val
        bags.append(bag)
    if str(mode or "contrastive").lower() != "contrastive":
        return bags
    vary: set[str] = set()
    for key in key_list:
        vals = []
        for bag in bags:
            v = bag.get(key)
            vals.append(json.dumps(v, ensure_ascii=False, sort_keys=True) if key in bag else None)
        if len(set(vals)) > 1:
            vary.add(key)
    return [{k: bag[k] for k in vary if k in bag} for bag in bags]


def _cell_paragraphs_of(spec: Dict[str, Any] | None) -> Dict[str, str]:
    raw = spec.get("cell_paragraphs") if isinstance(spec, dict) else None
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items() if str(v).strip()}


def _cell_paragraph_style(
    assigned: Any,
    *,
    spec: Dict[str, Any] | None,
    slot: str,
    para_entries: List[CatalogEntry],
    lookup: Dict[str, str],
    default_para: str | None,
) -> str | None:
    """Bind in-cell fonts from Tbl*.cell_paragraphs[slot], not name hints."""
    allowed = {e.style_id for e in para_entries}
    mapped = _resolve_style(_cell_paragraphs_of(spec).get(str(slot or "")), lookup)
    if mapped in allowed:
        return mapped
    resolved = _resolve_style(assigned, lookup) if assigned else None
    if resolved in allowed:
        return resolved
    return default_para if default_para in allowed else default_para


def _default_table_para(para_entries: List[CatalogEntry]) -> str | None:
    for entry in para_entries:
        blob = f"{entry.style_id} {entry.display_name or ''}".lower()
        if "data" in blob or "body" in blob or "value" in blob:
            return entry.style_id
    return para_entries[0].style_id if para_entries else None


def _cell_slot_catalog(catalog: Catalog, declarations: Dict[str, Any] | None) -> List[str]:
    slots: set[str] = set()
    for entry in catalog.by_layer("table"):
        spec = (declarations or {}).get(entry.style_id) or {}
        slots |= designed_slots(spec if isinstance(spec, dict) else {})
    if not slots:
        slots = {"header", "data", "label", "value", "stub", "note"}
    return sorted(slots)


def _slots_by_table(catalog: Catalog, declarations: Dict[str, Any] | None) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for entry in catalog.by_layer("table"):
        spec = (declarations or {}).get(entry.style_id) or {}
        names = sorted(designed_slots(spec if isinstance(spec, dict) else {}))
        out[entry.style_id] = names or ["header", "data"]
    return out


def _cell_paragraphs_by_table(catalog: Catalog, declarations: Dict[str, Any] | None) -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {}
    for entry in catalog.by_layer("table"):
        spec = (declarations or {}).get(entry.style_id) or {}
        mapped = _cell_paragraphs_of(spec if isinstance(spec, dict) else {})
        if mapped:
            out[entry.style_id] = mapped
    return out


def _llm_table_cells(
    spec: Dict[str, Any] | None,
    llm_cells: List[Any],
    *,
    para_entries: List[CatalogEntry],
    lookup: Dict[str, str],
    default_para: str | None,
) -> List[Dict[str, Any]]:
    """Keep LLM cell labels only, coerced into this Tbl*'s declared cell-styles."""
    spec = spec if isinstance(spec, dict) else {}
    allowed = designed_slots(spec)
    by_rc: Dict[tuple[int, int], Dict[str, Any]] = {}
    for cell in llm_cells or []:
        if not isinstance(cell, dict):
            continue
        try:
            row, col = int(cell.get("row")), int(cell.get("col"))
        except (TypeError, ValueError):
            continue
        slot = coerce_slot(cell.get("cell_style") or cell.get("slot"), allowed)
        psid = _cell_paragraph_style(
            cell.get("paragraph_style") or cell.get("style_id"),
            spec=spec,
            slot=slot,
            para_entries=para_entries,
            lookup=lookup,
            default_para=default_para,
        )
        by_rc[(row, col)] = {
            "row": row,
            "col": col,
            "cell_style": slot,
            "paragraph_style": psid,
        }
    return [by_rc[key] for key in sorted(by_rc)]


def _overlay_llm_cells(
    tbl: DocElement,
    spec: Dict[str, Any] | None,
    llm_cells: List[Any],
    *,
    para_entries: List[CatalogEntry],
    lookup: Dict[str, str],
    default_para: str | None,
) -> List[Dict[str, Any]]:
    del tbl
    return _llm_table_cells(
        spec,
        llm_cells,
        para_entries=para_entries,
        lookup=lookup,
        default_para=default_para,
    )


def _parallel_map(items: List[Any], worker, *, llm_workers: int) -> List[Any]:
    """Run ``worker`` over ``items``. ``LlmBudgetExceeded`` stops further work; partial results kept."""
    if not items:
        return []
    workers = max(1, int(llm_workers or 1))

    def _safe(item: Any) -> Any:
        try:
            return worker(item)
        except LlmBudgetExceeded:
            return _BUDGET_STOP
        except LlmJsonParseError:
            # Worker should usually swallow parse errors; keep other batches.
            return None

    if workers == 1 or len(items) <= 1:
        out: List[Any] = []
        for item in items:
            part = _safe(item)
            if part is _BUDGET_STOP:
                break
            if part is None:
                continue
            out.append(part)
        return out
    out = [None] * len(items)
    stop = False
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_safe, item): i for i, item in enumerate(items)}
        for fut in as_completed(futs):
            i = futs[fut]
            part = fut.result()
            if part is _BUDGET_STOP:
                stop = True
                out[i] = None
            else:
                out[i] = part
    return [p for p in out if p is not None]


_BUDGET_STOP = object()


def _j(obj: Any) -> str:
    return compact_json_dumps(obj)


def _normalize_run_items(raw: Any, lookup: Dict[str, str]) -> List[Dict[str, Any]]:
    spans: List[Dict[str, Any]] = []
    if not isinstance(raw, list):
        return spans
    for rr in raw:
        if not isinstance(rr, dict):
            continue
        rsid = _resolve_style(rr.get("run_style") or rr.get("style_id"), lookup)
        if not rsid:
            continue
        span = normalize_run_span(rr, run_style=rsid)
        if span:
            spans.append(span)
    return spans


def _merge_paragraph_list_items(
    items: Any,
    *,
    para_lookup: Dict[str, str],
    run_lookup: Dict[str, str],
    para_map: Dict[str, str],
    span_map: Dict[str, List[Dict[str, Any]]],
) -> None:
    if not isinstance(items, list):
        return
    for it in items:
        if not isinstance(it, dict):
            continue
        loc = it.get("location_id")
        if loc is None:
            continue
        psid = _resolve_style(
            it.get("paragraph_style") or it.get("style_id") or it.get("style_name"),
            para_lookup,
        )
        if psid:
            para_map.setdefault(str(loc), psid)
        spans = _normalize_run_items(it.get("runs") or [], run_lookup)
        if spans and str(loc) not in span_map:
            span_map[str(loc)] = spans


def _parsed_dict(
    result: Any,
    *,
    layer: str,
    language_model: Any,
    list_keys: tuple[str, ...] = (),
) -> Dict[str, Any]:
    try:
        parsed = parse_llm_json_strict(result, layer=layer, list_keys=list_keys)
    except LlmJsonParseError as err:
        _record_parse_failure(language_model, err)
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _with_feedback(user: str, feedback_note: str) -> str:
    note = str(feedback_note or "").strip()
    if not note:
        return user
    return user + "\n\nENVIRONMENT FEEDBACK:\n" + note


def _chat_parse(
    language_model: Any,
    *,
    system: str,
    user: str,
    llm_kwargs: Dict[str, Any],
    layer: str,
    list_keys: tuple[str, ...],
    env_feedback: bool,
    chat: Any | None = None,
) -> Dict[str, Any]:
    """One localization call. With ``env_feedback``, unparseable JSON is sent back once."""

    def _call(prompt_user: str) -> Any:
        if chat is not None:
            return chat(prompt_user)
        return language_model.chat_json(system=system, user=prompt_user, **llm_kwargs)

    result = _call(user)
    try:
        parsed = parse_llm_json_strict(result, layer=layer, list_keys=list_keys)
    except LlmJsonParseError as err:
        if not env_feedback:
            _record_parse_failure(language_model, err)
            return {}
        revised = (
            user
            + "\n\nERROR — previous output was not valid JSON or had the wrong structure:\n"
            + str(err)[:800]
            + "\nReturn one JSON object only."
        )
        result = _call(revised)
        try:
            parsed = parse_llm_json_strict(result, layer=layer, list_keys=list_keys)
        except LlmJsonParseError as err2:
            _record_parse_failure(language_model, err2)
            return {}
    return parsed if isinstance(parsed, dict) else {}


def _as_loc_map(value: Any, lookup: Dict[str, str]) -> Dict[str, str]:
    mapped: Dict[str, str] = {}
    if is_scalar_map(value):
        for loc, name in value.items():
            sid = _resolve_style(name, lookup)
            if sid:
                mapped[str(loc)] = sid
        return mapped
    if isinstance(value, list):
        return _map_simple_assignments(value, lookup)
    return mapped


def _loc_map_from_parsed(
    parsed: Dict[str, Any],
    lookup: Dict[str, str],
    *layer_keys: str,
    list_keys: tuple[str, ...] | None = None,
) -> Dict[str, str]:
    """Read compact ``{id: style_id}`` or legacy assignment arrays."""
    by_layer = parsed.get("by_layer")
    if isinstance(by_layer, dict):
        for key in layer_keys:
            mapped = _as_loc_map(by_layer.get(key), lookup)
            if mapped:
                return mapped
    for key in ("loc",) + layer_keys:
        mapped = _as_loc_map(parsed.get(key), lookup)
        if mapped:
            return mapped
    keys = list_keys if list_keys is not None else (
        "assignments",
        "items",
        "paragraphs",
        "sections",
        "images",
        "tables",
    )
    for key in keys:
        raw = parsed.get(key)
        if isinstance(raw, list) and raw:
            mapped = _map_simple_assignments(raw, lookup)
            if mapped:
                return mapped
        if is_scalar_map(raw):
            mapped = _as_loc_map(raw, lookup)
            if mapped:
                return mapped
    # Models often follow "keyed by location_id" and emit a bare {id: style_id} map.
    if is_scalar_map(parsed):
        return _as_loc_map(parsed, lookup)
    return {}


def _table_items_from_parsed(parsed: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Normalize compact table+table_cells maps or legacy ``tables`` arrays."""
    rows: List[Dict[str, Any]] = []
    tables_raw = parsed.get("tables") or parsed.get("table_assignments")
    if isinstance(tables_raw, list):
        rows.extend(it for it in tables_raw if isinstance(it, dict))
    elif isinstance(tables_raw, dict) and tables_raw and not is_scalar_map(tables_raw):
        for loc, rec in tables_raw.items():
            if isinstance(rec, dict):
                item = dict(rec)
                item.setdefault("location_id", loc)
                rows.append(item)
    cells_by_loc: Dict[str, Any] = {}
    table_cells = parsed.get("table_cells")
    if isinstance(table_cells, dict):
        cells_by_loc = {str(k): v for k, v in table_cells.items()}
    loc_map: Dict[str, Any] = {}
    for key in ("table", "loc"):
        raw = parsed.get(key)
        if is_scalar_map(raw):
            loc_map.update({str(k): v for k, v in raw.items()})
    if is_scalar_map(tables_raw):
        loc_map.update({str(k): v for k, v in tables_raw.items()})
    by_layer = parsed.get("by_layer")
    if isinstance(by_layer, dict) and is_scalar_map(by_layer.get("table")):
        loc_map.update({str(k): v for k, v in by_layer["table"].items()})
    if not loc_map and is_scalar_map(parsed):
        loc_map.update({str(k): v for k, v in parsed.items()})
    seen = {str(it.get("location_id")) for it in rows if it.get("location_id") is not None}
    for loc, style in loc_map.items():
        if loc in seen:
            for it in rows:
                if str(it.get("location_id")) == loc:
                    it.setdefault("table_style", style)
                    if loc in cells_by_loc and not it.get("cells"):
                        it["cells"] = cells_by_loc[loc]
                    break
            continue
        item: Dict[str, Any] = {"location_id": loc, "table_style": style}
        if loc in cells_by_loc:
            item["cells"] = cells_by_loc[loc]
        rows.append(item)
        seen.add(loc)
    if not rows and cells_by_loc:
        for loc, cells in cells_by_loc.items():
            rows.append({"location_id": loc, "cells": cells})
    return rows


def _map_simple_assignments(items: List[Any], lookup: Dict[str, str]) -> Dict[str, str]:
    mapped: Dict[str, str] = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        loc = it.get("location_id")
        if loc is None:
            continue
        sid = _resolve_style(it.get("style_id") or it.get("style_name") or it.get("section_style"), lookup)
        if sid:
            mapped[str(loc)] = sid
    return mapped


def assign_sections(
    *,
    catalog: Catalog,
    elements: List[DocElement],
    language_model,
    prompt: Dict[str, str],
    llm_kwargs: Dict[str, Any],
    skip_if_single: bool,
    cue_keys: List[str],
    cue_mode: str,
    env_feedback: bool = False,
    feedback_note: str = "",
) -> Dict[str, str]:
    entries = catalog.by_layer("section")
    if not elements or not entries:
        return {}
    if skip_if_single and len(entries) == 1:
        return {str(e.location_id): entries[0].style_id for e in elements}
    lookup = _name_to_id(entries)
    cues = contrastive_cues(elements, cue_keys, mode=cue_mode)
    payload = []
    for el, cue in zip(elements, cues):
        row = {"location_id": el.location_id, "content": (el.content or "")[:1600]}
        if cue:
            row["init_cues"] = cue
        payload.append(row)
    user = _user_prompt(prompt)
    user = user.replace("{{style_list_json}}", _j(_catalog_brief(entries)))
    user = user.replace("{{elements_json}}", _j(payload))
    user = user.replace("{{outline_json}}", "[]")
    user = _with_feedback(user, feedback_note)
    parsed = _chat_parse(
        language_model,
        system=prompt["system"],
        user=user,
        llm_kwargs=llm_kwargs,
        layer="section",
        list_keys=("assignments", "items"),
        env_feedback=env_feedback,
    )
    return _loc_map_from_parsed(parsed, lookup, "section")


def assign_paragraphs_with_runs(
    *,
    catalog: Catalog,
    paragraphs: List[DocElement],
    runs: List[DocElement],
    language_model,
    prompt: Dict[str, str],
    llm_kwargs: Dict[str, Any],
    batch_size: int,
    skip_if_single: bool,
    cue_keys: List[str],
    run_cue_keys: List[str],
    cue_mode: str,
    llm_workers: int,
    env_feedback: bool = False,
    feedback_note: str = "",
) -> tuple[Dict[str, str], Dict[str, List[Dict[str, Any]]]]:
    para_entries = catalog.by_layer("paragraph.body")
    run_entries = catalog.by_layer("run")
    para_map: Dict[str, str] = {}
    paragraph_runs: Dict[str, List[Dict[str, Any]]] = {}
    if not paragraphs or not para_entries:
        return para_map, paragraph_runs
    runs_by_para: Dict[str, List[DocElement]] = {}
    for run in runs:
        pid = str((run.meta or {}).get("para_location_id") or "")
        runs_by_para.setdefault(pid, []).append(run)
    # Run catalog nonempty: always ask the LLM to mark fragments, even if init has no delta runs.
    if skip_if_single and len(para_entries) == 1 and not run_entries:
        sid = para_entries[0].style_id
        return {str(p.location_id): sid for p in paragraphs}, {}

    para_lookup = _name_to_id(para_entries)
    run_lookup = _name_to_id(run_entries)
    n = max(1, int(batch_size or len(paragraphs) or 1))
    batches = [paragraphs[i : i + n] for i in range(0, len(paragraphs), n)]
    para_cues_all = contrastive_cues(paragraphs, cue_keys, mode=cue_mode)
    cue_by_loc = {str(p.location_id): c for p, c in zip(paragraphs, para_cues_all)}
    run_cue_allow = {str(k) for k in (run_cue_keys or []) if str(k).strip()}

    def _one(batch: List[DocElement]) -> tuple[Dict[str, str], Dict[str, List[Dict[str, Any]]]]:
        payload = []
        for para in batch:
            host_runs = runs_by_para.get(str(para.location_id)) or []
            row: Dict[str, Any] = {
                "location_id": para.location_id,
                "content": paragraph_payload_content(para),
            }
            cues = inventory_span_cues(para, host_runs)
            if run_cue_allow:
                for cue in cues:
                    props = cue.get("init_cues")
                    if isinstance(props, dict):
                        trimmed = {k: v for k, v in props.items() if k in run_cue_allow}
                        if trimmed:
                            cue["init_cues"] = trimmed
                        else:
                            cue.pop("init_cues", None)
            if cues:
                row["init_span_cues"] = cues
            cue = cue_by_loc.get(str(para.location_id)) or {}
            if cue:
                row["init_cues"] = cue
            payload.append(row)
        user = _user_prompt(prompt)
        user = user.replace("{{paragraph_styles_json}}", _j(_catalog_brief(para_entries)))
        user = user.replace("{{run_styles_json}}", _j(_catalog_brief(run_entries)))
        user = user.replace("{{elements_json}}", _j(payload))
        user = _with_feedback(user, feedback_note)
        parsed = _chat_parse(
            language_model,
            system=prompt["system"],
            user=user,
            llm_kwargs=llm_kwargs,
            layer="paragraph",
            list_keys=("assignments", "paragraphs", "items"),
            env_feedback=env_feedback,
        )
        local_para = _loc_map_from_parsed(parsed, para_lookup, "paragraph.body", "paragraph")
        local_spans: Dict[str, List[Dict[str, Any]]] = {}
        runs_payload = parsed.get("runs")
        if isinstance(runs_payload, dict):
            for loc, spans_raw in runs_payload.items():
                spans = _normalize_run_items(spans_raw, run_lookup)
                if spans:
                    local_spans[str(loc)] = spans
        for key in ("assignments", "paragraphs", "items"):
            _merge_paragraph_list_items(
                parsed.get(key),
                para_lookup=para_lookup,
                run_lookup=run_lookup,
                para_map=local_para,
                span_map=local_spans,
            )
        return local_para, local_spans

    for pmap, rmap in _parallel_map(batches, _one, llm_workers=llm_workers):
        para_map.update(pmap or {})
        paragraph_runs.update(rmap or {})
    return para_map, paragraph_runs


def assign_tables_joint(
    *,
    catalog: Catalog,
    tables: List[DocElement],
    declarations: Dict[str, Any] | None,
    language_model,
    prompt: Dict[str, str],
    llm_kwargs: Dict[str, Any],
    batch_size: int,
    skip_if_single: bool,
    context_paragraphs: int,
    llm_workers: int,
    section_map: Dict[str, str] | None = None,
    env_feedback: bool = False,
    feedback_note: str = "",
) -> tuple[Dict[str, str], Dict[str, List[Dict[str, Any]]]]:
    table_entries = catalog.by_layer("table")
    para_entries = catalog.by_layer("paragraph.table_cell")
    table_map: Dict[str, str] = {}
    cells_map: Dict[str, List[Dict[str, Any]]] = {}
    if not tables or not table_entries:
        return table_map, cells_map
    _ = skip_if_single  # hop 2 (cell-style) is always LLM recognition
    slots = _cell_slot_catalog(catalog, declarations)
    slots_by_table = _slots_by_table(catalog, declarations)
    paras_by_table = _cell_paragraphs_by_table(catalog, declarations)
    default_para = _default_table_para(para_entries)
    para_lookup = _name_to_id(para_entries)
    table_lookup = _name_to_id(table_entries)
    n = max(1, int(batch_size or len(tables) or 1))
    batches = [tables[i : i + n] for i in range(0, len(tables), n)]

    def _one(batch: List[DocElement]) -> tuple[Dict[str, str], Dict[str, List[Dict[str, Any]]]]:
        payload = []
        for tbl in batch:
            before, after = _trim_neighbors(tbl, context_paragraphs)
            cells = (tbl.meta or {}).get("cells") or []
            sec_idx = (tbl.meta or {}).get("section_index")
            sec_style = None
            if section_map and sec_idx not in (None, ""):
                sec_style = section_map.get(str(sec_idx))
                if not sec_style:
                    try:
                        sec_style = section_map.get(str(int(sec_idx)))
                    except (TypeError, ValueError):
                        sec_style = None
            hdr = header_row(tbl)
            caption = pick_caption(before, after)
            payload.append(
                {
                    "location_id": tbl.location_id,
                    "table_index": tbl.location_id,
                    "n_rows": (tbl.meta or {}).get("n_rows"),
                    "n_cols": (tbl.meta or {}).get("n_cols"),
                    "section_index": sec_idx,
                    "section_style": sec_style,
                    "caption": caption or None,
                    "header_row": hdr,
                    "before": before,
                    "after": after,
                    "cells": [
                        {
                            "row": c.get("row"),
                            "col": c.get("col"),
                            "text": (c.get("text") or "")[:200],
                        }
                        for c in cells
                    ],
                }
            )
        user = _user_prompt(prompt)
        user = user.replace("{{table_styles_json}}", _j(_catalog_brief(table_entries)))
        user = user.replace("{{paragraph_styles_json}}", _j(_catalog_brief(para_entries)))
        user = user.replace("{{cell_slots_json}}", _j(slots))
        user = user.replace("{{cell_slots_by_table_json}}", _j(slots_by_table))
        user = user.replace("{{cell_paragraphs_by_table_json}}", _j(paras_by_table))
        user = user.replace("{{elements_json}}", _j(payload))
        user = _with_feedback(user, feedback_note)
        parsed = _chat_parse(
            language_model,
            system=prompt["system"],
            user=user,
            llm_kwargs=llm_kwargs,
            layer="table",
            list_keys=("tables", "assignments", "items"),
            env_feedback=env_feedback,
        )
        local_t = _loc_map_from_parsed(parsed, table_lookup, "table")
        local_c: Dict[str, List[Dict[str, Any]]] = {}
        rows = _table_items_from_parsed(parsed)
        for it in rows:
            loc = it.get("location_id")
            if loc is None:
                continue
            tsid = _resolve_style(it.get("table_style") or it.get("style_id"), table_lookup)
            if tsid:
                local_t[str(loc)] = tsid
            src = next((t for t in batch if str(t.location_id) == str(loc)), None)
            spec = (declarations or {}).get(tsid) or {}
            if src is None:
                continue
            mapped = _overlay_llm_cells(
                src,
                spec,
                it.get("cells") or [],
                para_entries=para_entries,
                lookup=para_lookup,
                default_para=default_para,
            )
            if mapped:
                local_c[str(loc)] = mapped
        return local_t, local_c

    for tmap, cmap in _parallel_map(batches, _one, llm_workers=llm_workers):
        table_map.update(tmap or {})
        cells_map.update(cmap or {})
    return table_map, cells_map


def assign_images(
    *,
    catalog: Catalog,
    elements: List[DocElement],
    language_model,
    prompt: Dict[str, str],
    llm_kwargs: Dict[str, Any],
    batch_size: int,
    skip_if_single: bool,
    cue_keys: List[str],
    cue_mode: str,
    context_paragraphs: int,
    multimodal_model=None,
    llm_workers: int = 1,
    body_paragraphs: List[DocElement] | None = None,
    env_feedback: bool = False,
    feedback_note: str = "",
) -> Dict[str, str]:
    entries = catalog.by_layer("image")
    if not elements or not entries:
        return {}
    if skip_if_single and len(entries) == 1:
        return {str(e.location_id): entries[0].style_id for e in elements}
    lookup = _name_to_id(entries)
    style_list_json = _j(_catalog_brief(entries))
    n = max(1, int(batch_size or len(elements) or 1))
    batches = [elements[i : i + n] for i in range(0, len(elements), n)]
    cues_all = contrastive_cues(elements, cue_keys, mode=cue_mode)
    cue_by_loc = {str(e.location_id): c for e, c in zip(elements, cues_all)}

    def _one(batch: List[DocElement]) -> Dict[str, str]:
        payload = []
        for el in batch:
            before, after = _trim_neighbors(el, context_paragraphs, body_paragraphs)
            row = {
                "location_id": el.location_id,
                "picture_id": (el.meta or {}).get("picture_id") or (el.meta or {}).get("officecli_id"),
                "content": (el.content or "")[:300],
                "before": before,
                "after": after,
            }
            cue = cue_by_loc.get(str(el.location_id)) or {}
            if cue:
                row["init_cues"] = cue
            payload.append(row)
        user = _user_prompt(prompt)
        user = user.replace("{{style_list_json}}", style_list_json)
        user = user.replace("{{elements_json}}", _j(payload))
        user = user.replace("{{outline_json}}", "[]")
        user = _with_feedback(user, feedback_note)
        kwargs = dict(llm_kwargs)

        def _chat(prompt_user: str) -> Any:
            if multimodal_model is not None:
                from LongDocFmt.llm.openai_client import build_openai_multimodal_images_payload

                paths = [e.image_path for e in batch if e.image_path]
                images_payload = build_openai_multimodal_images_payload(paths) if paths else []
                return multimodal_model.chat_json(
                    system=prompt["system"], user=prompt_user, images=images_payload, **kwargs
                )
            return language_model.chat_json(system=prompt["system"], user=prompt_user, **kwargs)

        parsed = _chat_parse(
            multimodal_model or language_model,
            system=prompt["system"],
            user=user,
            llm_kwargs=kwargs,
            layer="image",
            list_keys=("assignments", "items"),
            env_feedback=env_feedback,
            chat=_chat,
        )
        return _loc_map_from_parsed(parsed, lookup, "image")

    mapped: Dict[str, str] = {}
    for part in _parallel_map(batches, _one, llm_workers=llm_workers):
        mapped.update(part or {})
    return mapped


def build_assignment(
    *,
    catalog: Catalog,
    init_elements: Dict[Layer, List[DocElement]],
    language_model,
    prompts: Dict[str, Dict[str, str]],
    llm_kwargs: Dict[str, Any],
    batch_sizes: Dict[str, Any],
    skip_if_single: bool = True,
    multimodal_model=None,
    mm_kwargs: Dict[str, Any] | None = None,
    llm_workers: int = 1,
    declarations: Dict[str, Any] | None = None,
    init_cues: Dict[str, Any] | None = None,
    table_context_paragraphs: int = 2,
    image_context_paragraphs: int = 2,
    env_feedback: bool = False,
    feedback_note: str = "",
) -> Assignment:
    """Build element→style map.

    Budget exceeded: stop further LLM, keep what is already assigned.
    JSON parse failure: retry that call once when ``env_feedback`` is set, otherwise
    skip **that call** (record it) and continue the next layer so apply can still
    run on partial loc.
    """
    assignment = Assignment()
    cue_cfg = dict(init_cues or {})
    cue_mode = str(cue_cfg.get("mode") or "contrastive")
    paras = [p for p in (init_elements.get("paragraph.body") or []) if _has_visible_text(p)]
    runs = list(init_elements.get("run") or [])
    tables = list(init_elements.get("table") or [])
    images = list(init_elements.get("image") or [])
    sections = list(init_elements.get("section") or [])

    def _stop() -> bool:
        return budget_exhausted(language_model) or budget_exhausted(multimodal_model)

    section_prompt = prompts.get("section")
    if section_prompt and sections and not _stop():
        try:
            assignment.by_layer["section"] = assign_sections(
                catalog=catalog,
                elements=sections,
                language_model=language_model,
                prompt=section_prompt,
                llm_kwargs=llm_kwargs,
                skip_if_single=skip_if_single,
                cue_keys=list(cue_cfg.get("section") or []),
                cue_mode=cue_mode,
                env_feedback=env_feedback,
                feedback_note=feedback_note,
            )
        except LlmBudgetExceeded:
            return assignment
        except LlmJsonParseError as err:
            _record_parse_failure(language_model, err)

    para_prompt = prompts.get("paragraph_with_runs") or prompts.get("paragraph.body")
    if para_prompt and paras and not _stop():
        try:
            para_map, paragraph_runs = assign_paragraphs_with_runs(
                catalog=catalog,
                paragraphs=paras,
                runs=runs,
                language_model=language_model,
                prompt=para_prompt,
                llm_kwargs=llm_kwargs,
                batch_size=_batch_size(batch_sizes, "paragraph.body", len(paras), None),
                skip_if_single=skip_if_single,
                cue_keys=list(cue_cfg.get("paragraph.body") or []),
                run_cue_keys=list(cue_cfg.get("run") or []),
                cue_mode=cue_mode,
                llm_workers=llm_workers,
                env_feedback=env_feedback,
                feedback_note=feedback_note,
            )
            assignment.by_layer["paragraph.body"] = para_map
            if paragraph_runs:
                assignment.paragraph_runs = paragraph_runs
        except LlmBudgetExceeded:
            return assignment
        except LlmJsonParseError as err:
            _record_parse_failure(language_model, err)

    table_prompt = prompts.get("table_joint") or prompts.get("table")
    if table_prompt and tables and not _stop():
        try:
            tmap, cmap = assign_tables_joint(
                catalog=catalog,
                tables=tables,
                declarations=declarations,
                language_model=language_model,
                prompt=table_prompt,
                llm_kwargs=llm_kwargs,
                batch_size=_batch_size(batch_sizes, "table", len(tables), None),
                skip_if_single=skip_if_single,
                context_paragraphs=int(table_context_paragraphs or 0),
                llm_workers=llm_workers,
                section_map=assignment.by_layer.get("section") or {},
                env_feedback=env_feedback,
                feedback_note=feedback_note,
            )
            assignment.by_layer["table"] = tmap
            assignment.table_cells = cmap
        except LlmBudgetExceeded:
            return assignment
        except LlmJsonParseError as err:
            _record_parse_failure(language_model, err)

    image_prompt = prompts.get("image")
    if image_prompt and images and not _stop():
        try:
            assignment.by_layer["image"] = assign_images(
                catalog=catalog,
                elements=images,
                language_model=language_model,
                prompt=image_prompt,
                llm_kwargs=mm_kwargs or llm_kwargs,
                batch_size=_batch_size(batch_sizes, "image", len(images), None),
                skip_if_single=skip_if_single,
                cue_keys=list(cue_cfg.get("image") or []),
                cue_mode=cue_mode,
                context_paragraphs=int(image_context_paragraphs or 0),
                multimodal_model=multimodal_model,
                llm_workers=llm_workers,
                body_paragraphs=paras,
                env_feedback=env_feedback,
                feedback_note=feedback_note,
            )
        except LlmBudgetExceeded:
            return assignment
        except LlmJsonParseError as err:
            _record_parse_failure(multimodal_model or language_model, err)
    return assignment


def assign_layer(
    *,
    layer: Layer,
    catalog: Catalog,
    elements: List[DocElement],
    language_model,
    prompt: Dict[str, str],
    llm_kwargs: Dict[str, Any],
    batch_size: int,
    outline: List[Dict[str, Any]] | None = None,
    skip_if_single: bool = True,
    multimodal_model=None,
    use_vision: bool = False,
    image_batch_size: int = 6,
    llm_workers: int = 1,
    env_feedback: bool = False,
    feedback_note: str = "",
) -> Dict[str, str]:
    """Content-only fallback (template-bind). Does not use a global outline."""
    del outline
    if layer == "image":
        return assign_images(
            catalog=catalog,
            elements=elements,
            language_model=language_model,
            prompt=prompt,
            llm_kwargs=llm_kwargs,
            batch_size=int(image_batch_size or batch_size or len(elements) or 1),
            skip_if_single=skip_if_single,
            cue_keys=[],
            cue_mode="contrastive",
            context_paragraphs=0,
            multimodal_model=multimodal_model if use_vision else None,
            llm_workers=llm_workers,
            env_feedback=env_feedback,
            feedback_note=feedback_note,
        )
    if layer == "section":
        return assign_sections(
            catalog=catalog,
            elements=elements,
            language_model=language_model,
            prompt=prompt,
            llm_kwargs=llm_kwargs,
            skip_if_single=skip_if_single,
            cue_keys=[],
            cue_mode="contrastive",
            env_feedback=env_feedback,
            feedback_note=feedback_note,
        )
    entries = catalog.by_layer(layer)
    if not elements or not entries:
        return {}
    if skip_if_single and len(entries) == 1:
        return {str(e.location_id): entries[0].style_id for e in elements}
    lookup = _name_to_id(entries)
    n = max(1, int(batch_size or len(elements) or 1))
    batches = [elements[i : i + n] for i in range(0, len(elements), n)]
    style_list_json = _j(_catalog_brief(entries))

    def _one(batch: List[DocElement]) -> Dict[str, str]:
        payload = [{"location_id": e.location_id, "content": (e.content or "")[:400]} for e in batch]
        user = _user_prompt(prompt)
        user = user.replace("{{style_list_json}}", style_list_json)
        user = user.replace("{{elements_json}}", _j(payload))
        user = user.replace("{{outline_json}}", "[]")
        user = _with_feedback(user, feedback_note)
        parsed = _chat_parse(
            language_model,
            system=prompt["system"],
            user=user,
            llm_kwargs=llm_kwargs,
            layer=str(layer),
            list_keys=("assignments", "items"),
            env_feedback=env_feedback,
        )
        return _loc_map_from_parsed(parsed, lookup, str(layer))

    mapped: Dict[str, str] = {}
    for part in _parallel_map(batches, _one, llm_workers=llm_workers):
        mapped.update(part or {})
    return mapped


def build_assignment_flat(
    *,
    catalog: Catalog,
    init_elements: Dict[Layer, List[DocElement]],
    language_model,
    prompts: Dict[str, Dict[str, str]],
    llm_kwargs: Dict[str, Any],
    skip_if_single: bool = True,
    declarations: Dict[str, Any] | None = None,
    init_cues: Dict[str, Any] | None = None,
    table_context_paragraphs: int = 2,
    image_context_paragraphs: int = 2,
    env_feedback: bool = False,
    feedback_note: str = "",
    **_ignored: Any,
) -> Assignment:
    """Map all init elements to styles in **one** LLM call (no per-layer split).

    Used by ``assign_flat`` / AblationStudy NoLayeredLoc. Same Assignment schema
    as ``build_assignment`` so Apply stays unchanged. Extra kwargs are ignored
    so callers can pass the layered signature (batch_sizes, llm_workers, …).
    """
    del _ignored
    assignment = Assignment()
    cue_cfg = dict(init_cues or {})
    cue_mode = str(cue_cfg.get("mode") or "contrastive")
    paras = [p for p in (init_elements.get("paragraph.body") or []) if _has_visible_text(p)]
    runs = list(init_elements.get("run") or [])
    tables = list(init_elements.get("table") or [])
    images = list(init_elements.get("image") or [])
    sections = list(init_elements.get("section") or [])

    section_entries = catalog.by_layer("section")
    para_entries = catalog.by_layer("paragraph.body")
    run_entries = catalog.by_layer("run")
    table_entries = catalog.by_layer("table")
    cell_para_entries = catalog.by_layer("paragraph.table_cell")
    image_entries = catalog.by_layer("image")

    if skip_if_single:
        if sections and len(section_entries) == 1:
            assignment.by_layer["section"] = {
                str(e.location_id): section_entries[0].style_id for e in sections
            }
            sections = []
        if paras and len(para_entries) == 1 and not run_entries:
            assignment.by_layer["paragraph.body"] = {
                str(p.location_id): para_entries[0].style_id for p in paras
            }
            paras = []
        if images and len(image_entries) == 1:
            assignment.by_layer["image"] = {
                str(e.location_id): image_entries[0].style_id for e in images
            }
            images = []

    if not (sections or paras or tables or images):
        return assignment

    prompt = prompts.get("flat") or prompts.get("element_loc_flat") or {}
    if not (prompt.get("system") or prompt.get("user_template")):
        raise ValueError("build_assignment_flat requires prompts['flat'] with system/user_template")

    runs_by_para: Dict[str, List[DocElement]] = {}
    for run in runs:
        pid = str((run.meta or {}).get("para_location_id") or "")
        runs_by_para.setdefault(pid, []).append(run)
    run_cue_allow = {str(k) for k in (cue_cfg.get("run") or []) if str(k).strip()}
    para_cues = contrastive_cues(paras, list(cue_cfg.get("paragraph.body") or []), mode=cue_mode)
    cue_by_para = {str(p.location_id): c for p, c in zip(paras, para_cues)}
    section_cues = contrastive_cues(sections, list(cue_cfg.get("section") or []), mode=cue_mode)
    image_cues = contrastive_cues(images, list(cue_cfg.get("image") or []), mode=cue_mode)

    section_payload = []
    for el, cue in zip(sections, section_cues):
        row: Dict[str, Any] = {"location_id": el.location_id, "content": (el.content or "")[:1600]}
        if cue:
            row["init_cues"] = cue
        section_payload.append(row)

    para_payload = []
    for para in paras:
        host_runs = runs_by_para.get(str(para.location_id)) or []
        row = {
            "location_id": para.location_id,
            "content": paragraph_payload_content(para),
        }
        cues = inventory_span_cues(para, host_runs)
        if run_cue_allow:
            for cue in cues:
                props = cue.get("init_cues")
                if isinstance(props, dict):
                    trimmed = {k: v for k, v in props.items() if k in run_cue_allow}
                    if trimmed:
                        cue["init_cues"] = trimmed
                    else:
                        cue.pop("init_cues", None)
        if cues:
            row["init_span_cues"] = cues
        cue = cue_by_para.get(str(para.location_id)) or {}
        if cue:
            row["init_cues"] = cue
        para_payload.append(row)

    table_payload = []
    for tbl in tables:
        before, after = _trim_neighbors(tbl, int(table_context_paragraphs or 0))
        cells = (tbl.meta or {}).get("cells") or []
        table_payload.append(
            {
                "location_id": tbl.location_id,
                "n_rows": (tbl.meta or {}).get("n_rows"),
                "n_cols": (tbl.meta or {}).get("n_cols"),
                "section_index": (tbl.meta or {}).get("section_index"),
                "caption": pick_caption(before, after) or None,
                "header_row": header_row(tbl),
                "before": before,
                "after": after,
                "cells": [
                    {"row": c.get("row"), "col": c.get("col"), "text": (c.get("text") or "")[:200]}
                    for c in cells
                ],
            }
        )

    image_payload = []
    for el, cue in zip(images, image_cues):
        before, after = _trim_neighbors(el, int(image_context_paragraphs or 0), paras)
        row = {
            "location_id": el.location_id,
            "picture_id": (el.meta or {}).get("picture_id") or (el.meta or {}).get("officecli_id"),
            "content": (el.content or "")[:300],
            "before": before,
            "after": after,
        }
        if cue:
            row["init_cues"] = cue
        image_payload.append(row)

    slots = _cell_slot_catalog(catalog, declarations)
    slots_by_table = _slots_by_table(catalog, declarations)
    paras_by_table = _cell_paragraphs_by_table(catalog, declarations)

    user = _user_prompt(prompt)
    user = user.replace("{{section_styles_json}}", _j(_catalog_brief(section_entries)))
    user = user.replace("{{paragraph_styles_json}}", _j(_catalog_brief(para_entries)))
    user = user.replace("{{run_styles_json}}", _j(_catalog_brief(run_entries)))
    user = user.replace("{{table_styles_json}}", _j(_catalog_brief(table_entries)))
    user = user.replace(
        "{{table_cell_paragraph_styles_json}}",
        _j(_catalog_brief(cell_para_entries)),
    )
    user = user.replace("{{image_styles_json}}", _j(_catalog_brief(image_entries)))
    user = user.replace("{{cell_slots_json}}", _j(slots))
    user = user.replace("{{cell_slots_by_table_json}}", _j(slots_by_table))
    user = user.replace("{{cell_paragraphs_by_table_json}}", _j(paras_by_table))
    user = user.replace("{{sections_json}}", _j(section_payload))
    user = user.replace("{{paragraphs_json}}", _j(para_payload))
    user = user.replace("{{tables_json}}", _j(table_payload))
    user = user.replace("{{images_json}}", _j(image_payload))
    user = _with_feedback(user, feedback_note)

    parsed = _chat_parse(
        language_model,
        system=prompt["system"],
        user=user,
        llm_kwargs=llm_kwargs,
        layer="flat",
        list_keys=(),
        env_feedback=env_feedback,
    )
    if not parsed:
        return assignment

    section_lookup = _name_to_id(section_entries)
    para_lookup = _name_to_id(para_entries)
    run_lookup = _name_to_id(run_entries)
    table_lookup = _name_to_id(table_entries)
    cell_para_lookup = _name_to_id(cell_para_entries)
    image_lookup = _name_to_id(image_entries)
    default_para = _default_table_para(cell_para_entries)

    mapped_sec = _loc_map_from_parsed(
        parsed,
        section_lookup,
        "section",
        list_keys=("sections", "section_assignments"),
    )
    if mapped_sec:
        assignment.by_layer["section"] = {
            **(assignment.by_layer.get("section") or {}),
            **mapped_sec,
        }

    para_map = dict(assignment.by_layer.get("paragraph.body") or {})
    paragraph_runs: Dict[str, List[Dict[str, Any]]] = dict(assignment.paragraph_runs or {})
    mapped_para = _loc_map_from_parsed(
        parsed,
        para_lookup,
        "paragraph.body",
        "paragraph",
        list_keys=("paragraphs", "paragraph_assignments"),
    )
    para_map.update(mapped_para)
    runs_payload = parsed.get("runs")
    if isinstance(runs_payload, dict):
        for loc, spans_raw in runs_payload.items():
            spans = _normalize_run_items(spans_raw, run_lookup)
            if spans:
                paragraph_runs[str(loc)] = spans
    for key in ("paragraphs", "paragraph_assignments"):
        _merge_paragraph_list_items(
            parsed.get(key),
            para_lookup=para_lookup,
            run_lookup=run_lookup,
            para_map=para_map,
            span_map=paragraph_runs,
        )
    if para_map:
        assignment.by_layer["paragraph.body"] = para_map
    if paragraph_runs:
        assignment.paragraph_runs = paragraph_runs

    table_map: Dict[str, str] = dict(assignment.by_layer.get("table") or {})
    table_map.update(_loc_map_from_parsed(parsed, table_lookup, "table"))
    cells_map: Dict[str, List[Dict[str, Any]]] = dict(assignment.table_cells or {})
    table_rows = _table_items_from_parsed(parsed)
    if table_rows and tables:
        for it in table_rows:
            loc = it.get("location_id")
            if loc is None:
                continue
            tsid = _resolve_style(it.get("table_style") or it.get("style_id"), table_lookup)
            if tsid:
                table_map[str(loc)] = tsid
            src = next((t for t in tables if str(t.location_id) == str(loc)), None)
            if src is None or not tsid:
                continue
            spec = (declarations or {}).get(tsid) or {}
            mapped_cells = _overlay_llm_cells(
                src,
                spec,
                it.get("cells") or [],
                para_entries=cell_para_entries,
                lookup=cell_para_lookup,
                default_para=default_para,
            )
            if mapped_cells:
                cells_map[str(loc)] = mapped_cells
    if table_map:
        assignment.by_layer["table"] = table_map
    if cells_map:
        assignment.table_cells = cells_map

    mapped_img = _loc_map_from_parsed(
        parsed,
        image_lookup,
        "image",
        list_keys=("images", "image_assignments"),
    )
    if mapped_img:
        assignment.by_layer["image"] = {
            **(assignment.by_layer.get("image") or {}),
            **mapped_img,
        }

    return assignment

