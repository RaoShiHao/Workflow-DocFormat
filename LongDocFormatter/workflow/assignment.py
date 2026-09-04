"""Assign init instances to catalog styles.

Units (one LLM call per object layer unless batch_sizes sets an int):
- section: whole layer
- paragraph.body + run: all paragraphs (style + inline fragments together)
- table: all tables (structure + per-cell slots + in-cell paragraph styles)
- image: all pictures + nearby body paragraphs
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, Iterable, List

from LongDocFormatter.workflow.catalog import header_row, pick_caption
from LongDocFormatter.workflow.cell_plan import coerce_slot, designed_slots
from LongDocFormatter.workflow.contracts import Assignment, Catalog, CatalogEntry, DocElement, Layer
from LongDocFormatter.workflow.json_util import LlmJsonParseError, parse_llm_json_strict
from LongDocFormatter.workflow.llm_trace import LlmBudgetExceeded, budget_exhausted, logger_of
from LongDocFormatter.workflow.run_span import inventory_span_cues, normalize_run_span, paragraph_payload_content


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
    parsed = parse_llm_json_strict(result, layer=layer)
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


def _cell_paragraph_style(
    assigned: Any,
    *,
    slot: str,
    row: Any,
    para_entries: List[CatalogEntry],
    lookup: Dict[str, str],
    default_para: str | None,
) -> str | None:
    """Keep table-paragraph on Para*Cell roles (AutoDataBuild table-cell-warning rule)."""
    allowed = {e.style_id for e in para_entries}
    resolved = _resolve_style(assigned, lookup) if assigned else None
    slot_l = str(slot or "").lower()
    row_n = None
    try:
        row_n = int(row)
    except (TypeError, ValueError):
        pass
    if row_n == 1 and slot_l not in {"label", "value", "stub", "note"}:
        slot_l = slot_l or "header"

    def _by_hint(*needles: str) -> str | None:
        for entry in para_entries:
            blob = f"{entry.style_id} {entry.display_name or ''}".lower()
            if any(n in blob for n in needles):
                return entry.style_id
        return None

    if slot_l in {"header", "head"}:
        hinted = _by_hint("header", "head")
        if hinted:
            return hinted
    elif slot_l == "label":
        hinted = _by_hint("label")
        if hinted:
            return hinted
    elif slot_l == "stub":
        hinted = _by_hint("stub", "label")
        if hinted:
            return hinted
    elif slot_l == "note":
        hinted = _by_hint("note")
        if hinted:
            return hinted
    elif slot_l in {"value", "data"}:
        hinted = _by_hint("data", "value", "body")
        if hinted:
            return hinted
    if resolved in allowed:
        return resolved
    return default_para


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
            slot=slot,
            row=row,
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
    user = user.replace("{{style_list_json}}", json.dumps(_catalog_brief(entries), ensure_ascii=False))
    user = user.replace("{{elements_json}}", json.dumps(payload, ensure_ascii=False))
    user = user.replace("{{outline_json}}", "[]")
    result = language_model.chat_json(system=prompt["system"], user=user, **llm_kwargs)
    return _map_simple_assignments(
        _parsed_list_or_empty(
            result, "assignments", "items", layer="section", language_model=language_model
        ),
        lookup,
    )


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
        user = user.replace("{{paragraph_styles_json}}", json.dumps(_catalog_brief(para_entries), ensure_ascii=False))
        user = user.replace("{{run_styles_json}}", json.dumps(_catalog_brief(run_entries), ensure_ascii=False))
        user = user.replace("{{elements_json}}", json.dumps(payload, ensure_ascii=False))
        result = language_model.chat_json(system=prompt["system"], user=user, **llm_kwargs)
        local_para: Dict[str, str] = {}
        local_spans: Dict[str, List[Dict[str, Any]]] = {}
        for it in _parsed_list_or_empty(
            result,
            "assignments",
            "paragraphs",
            "items",
            layer="paragraph",
            language_model=language_model,
        ):
            if not isinstance(it, dict):
                continue
            loc = it.get("location_id")
            psid = _resolve_style(
                it.get("paragraph_style") or it.get("style_id") or it.get("style_name"),
                para_lookup,
            )
            if loc is not None and psid:
                local_para[str(loc)] = psid
            if loc is None:
                continue
            spans: List[Dict[str, Any]] = []
            for rr in it.get("runs") or []:
                if not isinstance(rr, dict):
                    continue
                rsid = _resolve_style(rr.get("run_style") or rr.get("style_id"), run_lookup)
                if not rsid:
                    continue
                span = normalize_run_span(rr, run_style=rsid)
                if span:
                    spans.append(span)
            if spans:
                local_spans[str(loc)] = spans
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
        user = user.replace("{{table_styles_json}}", json.dumps(_catalog_brief(table_entries), ensure_ascii=False))
        user = user.replace("{{paragraph_styles_json}}", json.dumps(_catalog_brief(para_entries), ensure_ascii=False))
        user = user.replace("{{cell_slots_json}}", json.dumps(slots, ensure_ascii=False))
        user = user.replace("{{cell_slots_by_table_json}}", json.dumps(slots_by_table, ensure_ascii=False))
        user = user.replace("{{elements_json}}", json.dumps(payload, ensure_ascii=False))
        result = language_model.chat_json(system=prompt["system"], user=user, **llm_kwargs)
        local_t: Dict[str, str] = {}
        local_c: Dict[str, List[Dict[str, Any]]] = {}
        rows = _parsed_list_or_empty(
            result, "tables", "assignments", "items", layer="table", language_model=language_model
        )
        for it in rows:
            if not isinstance(it, dict):
                continue
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
) -> Dict[str, str]:
    entries = catalog.by_layer("image")
    if not elements or not entries:
        return {}
    if skip_if_single and len(entries) == 1:
        return {str(e.location_id): entries[0].style_id for e in elements}
    lookup = _name_to_id(entries)
    style_list_json = json.dumps(_catalog_brief(entries), ensure_ascii=False)
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
        user = user.replace("{{elements_json}}", json.dumps(payload, ensure_ascii=False))
        user = user.replace("{{outline_json}}", "[]")
        kwargs = dict(llm_kwargs)
        if multimodal_model is not None:
            from LongDocFormatter.llm.openai_client import build_openai_multimodal_images_payload

            paths = [e.image_path for e in batch if e.image_path]
            images_payload = build_openai_multimodal_images_payload(paths) if paths else []
            result = multimodal_model.chat_json(
                system=prompt["system"], user=user, images=images_payload, **kwargs
            )
        else:
            result = language_model.chat_json(system=prompt["system"], user=user, **kwargs)
        return _map_simple_assignments(
            _parsed_list_or_empty(
                result,
                "assignments",
                "items",
                layer="image",
                language_model=multimodal_model or language_model,
            ),
            lookup,
        )

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
) -> Assignment:
    """Build element→style map.

    Budget exceeded: stop further LLM, keep what is already assigned.
    JSON parse failure: skip **that call** (record it), continue the next layer
    so apply can still run on partial loc.
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
        )
    entries = catalog.by_layer(layer)
    if not elements or not entries:
        return {}
    if skip_if_single and len(entries) == 1:
        return {str(e.location_id): entries[0].style_id for e in elements}
    lookup = _name_to_id(entries)
    n = max(1, int(batch_size or len(elements) or 1))
    batches = [elements[i : i + n] for i in range(0, len(elements), n)]
    style_list_json = json.dumps(_catalog_brief(entries), ensure_ascii=False)

    def _one(batch: List[DocElement]) -> Dict[str, str]:
        payload = [{"location_id": e.location_id, "content": (e.content or "")[:400]} for e in batch]
        user = _user_prompt(prompt)
        user = user.replace("{{style_list_json}}", style_list_json)
        user = user.replace("{{elements_json}}", json.dumps(payload, ensure_ascii=False))
        user = user.replace("{{outline_json}}", "[]")
        result = language_model.chat_json(system=prompt["system"], user=user, **llm_kwargs)
        return _map_simple_assignments(
            _parsed_list_or_empty(
                result, "assignments", "items", layer=str(layer), language_model=language_model
            ),
            lookup,
        )

    mapped: Dict[str, str] = {}
    for part in _parallel_map(batches, _one, llm_workers=llm_workers):
        mapped.update(part or {})
    return mapped

