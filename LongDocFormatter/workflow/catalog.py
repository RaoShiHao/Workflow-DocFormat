from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Tuple

from LongDocFormatter.workflow.cell_plan import infer_table_style, table_role_signature
from LongDocFormatter.workflow.contracts import Catalog, CatalogEntry, DocElement, LAYER_ORDER, Layer
from LongDocFormatter.workflow.json_util import (
    LlmJsonParseError,
    llm_content_text,
    parse_llm_json,
    parse_llm_json_strict,
    stable_json_dumps,
)
from LongDocFormatter.workflow.llm_trace import logger_of
from LongDocFormatter.workflow.whitelist import filter_props, whitelist_keys

_PREFIX = {
    "section": "Sec",
    "paragraph.body": "Para",
    "paragraph.table_cell": "Para",
    "table": "Tbl",
    "image": "Img",
    "run": "Run",
}


def signature_of(props: Dict[str, Any], layer: Layer, element: DocElement | None = None) -> str:
    keys = whitelist_keys(layer)
    if layer == "table":
        row = element.to_dict() if element is not None else {"props": props, "meta": {}}
        designed = infer_table_style(row)
        blob = table_role_signature(
            props.get("table_format") or {},
            designed.get("cells") or {},
            designed.get("cell_style_plan") or {},
        )
    else:
        blob = filter_props(props, keys)
    return hashlib.md5(stable_json_dumps(blob).encode("utf-8")).hexdigest()


def cluster_elements(elements: List[DocElement]) -> List[List[DocElement]]:
    buckets: Dict[str, List[DocElement]] = {}
    order: List[str] = []
    for el in elements:
        sig = signature_of(el.props, el.layer, element=el)
        if sig not in buckets:
            buckets[sig] = []
            order.append(sig)
        buckets[sig].append(el)
    return [buckets[s] for s in order]


def slug_style_id(layer: Layer, name: str, used: set[str]) -> str:
    prefix = _PREFIX.get(layer, "St")
    raw = re.sub(r"[^A-Za-z0-9]+", "", (name or "").title()) or "Style"
    if layer == "paragraph.table_cell" and not raw.endswith("Cell"):
        raw += "Cell"
    sid = prefix + raw
    if sid not in used:
        used.add(sid)
        return sid
    n = 2
    while f"{sid}{n}" in used:
        n += 1
    out = f"{sid}{n}"
    used.add(out)
    return out


def _parse_naming(result: Dict[str, Any]) -> Dict[int, Dict[str, str]]:
    parsed = parse_llm_json(result)
    items = parsed.get("styles") or parsed.get("results") or parsed.get("items") or []
    out: Dict[int, Dict[str, str]] = {}
    if not isinstance(items, list):
        return out
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            sid = int(it.get("cluster_id") or it.get("style_id"))
        except (TypeError, ValueError):
            continue
        out[sid] = {
            "style_name": str(it.get("style_name") or it.get("display_name") or f"style_{sid}"),
            "description": str(it.get("description") or ""),
            "display_name": str(it.get("display_name") or it.get("style_name") or ""),
        }
    return out


def _cluster_content(
    cluster: List[DocElement],
    *,
    max_examples: int,
    max_chars: int,
) -> str:
    texts = [
        (e.content or "").strip()
        for e in cluster[: max(1, int(max_examples or 3))]
        if (e.content or "").strip()
    ]
    return "\n".join(texts)[: max(1, int(max_chars or 800))]


def _format_properties(layer: Layer, rep: DocElement) -> Dict[str, Any]:
    if layer == "table":
        designed = infer_table_style(rep.to_dict())
        return {
            "table_format": (rep.props or {}).get("table_format") or {},
            "cells": designed.get("cells") or {},
            "cell_style_plan": designed.get("cell_style_plan") or {},
        }
    if layer == "image":
        props = dict(rep.props or {})
        return {k: props[k] for k in ("width", "height", "hAlign") if k in props}
    return dict(rep.props or {})


def _declaration_for(layer: Layer, rep: DocElement) -> Dict[str, Any]:
    if layer == "table":
        designed = infer_table_style(rep.to_dict())
        payload: Dict[str, Any] = {
            "object": "table",
            "table_format": (rep.props or {}).get("table_format") or {},
            "cells": designed.get("cells") or {},
            "cell_style_plan": designed.get("cell_style_plan") or {},
        }
        if designed.get("cell_paragraphs"):
            payload["cell_paragraphs"] = designed["cell_paragraphs"]
        return payload
    return dict(rep.props or {})


def _section_loc_to_cluster(section_clusters: List[List[DocElement]]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for i, cluster in enumerate(section_clusters, start=1):
        for el in cluster:
            out[str(el.location_id)] = i
    return out


def _table_section_cluster_ids(
    cluster: List[DocElement],
    section_loc_to_cluster: Dict[str, int],
) -> List[int]:
    ids: List[int] = []
    for el in cluster:
        si = (el.meta or {}).get("section_index")
        if si in (None, "", 0):
            continue
        cid = section_loc_to_cluster.get(str(si))
        if cid is not None and cid not in ids:
            ids.append(cid)
    return ids


_CAPTION_RE = re.compile(
    r"(?is)^\s*(?:table|tbl|tab\.?|figure|fig\.?|exhibit|附表?|表|图)\s*[.\-:]?\s*\d"
)


def _clip_text(text: Any, n: int = 160) -> str:
    s = str(text or "").replace("\n", " ").strip()
    return s[:n] if s else ""


def is_caption_text(text: str) -> bool:
    return bool(_CAPTION_RE.search(text or ""))


def _neighbor_texts(el: DocElement) -> List[str]:
    meta = el.meta or {}
    out: List[str] = []
    for key in ("neighbor_before", "neighbor_after"):
        for row in meta.get(key) or []:
            if not isinstance(row, dict):
                continue
            t = _clip_text(row.get("content"))
            if t:
                out.append(t)
    return out


def caption_texts(el: DocElement) -> List[str]:
    neighbors = _neighbor_texts(el)
    caps = [t for t in neighbors if is_caption_text(t)]
    return caps or neighbors


def header_row(el: DocElement, *, max_cols: int = 12, max_chars: int = 80) -> List[str]:
    cells = (el.meta or {}).get("cells") or []
    row1: List[tuple[int, str]] = []
    for cell in cells:
        if not isinstance(cell, dict):
            continue
        try:
            if int(cell.get("row") or 0) != 1:
                continue
            col = int(cell.get("col") or 0)
        except (TypeError, ValueError):
            continue
        text = _clip_text(cell.get("text"), max_chars)
        if text:
            row1.append((col, text))
    row1.sort(key=lambda item: item[0])
    return [text for _col, text in row1[:max_cols]]


def join_header_row(cells: List[str]) -> str:
    return " | ".join(cells)


def pick_caption(before: List[Any], after: List[Any]) -> str:
    texts: List[str] = []
    ordered = list(after or []) + list(reversed(before or []))
    for row in ordered:
        if isinstance(row, dict):
            text = _clip_text(row.get("content"))
        else:
            text = _clip_text(row)
        if text:
            texts.append(text)
    for text in texts:
        if is_caption_text(text):
            return text
    return texts[0] if texts else ""


def _unique_texts(items: List[str], *, limit: int = 6) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for item in items:
        key = item.lower()
        if not item or key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def cluster_table_usage(cluster: List[DocElement]) -> Dict[str, List[str]]:
    captions: List[str] = []
    header_rows: List[str] = []
    for el in cluster:
        captions.extend(caption_texts(el))
        hdr = header_row(el)
        if hdr:
            header_rows.append(join_header_row(hdr))
    return {
        "captions": _unique_texts(captions),
        "header_rows": _unique_texts(header_rows),
    }


def _copy_table_usage(entry: CatalogEntry, spec: Dict[str, Any] | None) -> None:
    if not isinstance(spec, dict):
        return
    if entry.caption_type:
        spec["caption_type"] = entry.caption_type
    if entry.header_semantics:
        spec["header_semantics"] = entry.header_semantics
    if entry.captions:
        spec["captions"] = list(entry.captions)
    if entry.header_rows:
        spec["header_rows"] = list(entry.header_rows)
    if entry.typical_sections:
        spec["typical_sections"] = list(entry.typical_sections)


def _parse_joint_naming(result: Any) -> Dict[tuple[str, int], Dict[str, Any]]:
    parsed = parse_llm_json(result)
    items = parsed.get("styles") or parsed.get("results") or parsed.get("items") or []
    out: Dict[tuple[str, int], Dict[str, Any]] = {}
    if not isinstance(items, list):
        return out
    for it in items:
        if not isinstance(it, dict):
            continue
        layer = str(it.get("object") or it.get("layer") or "").strip()
        raw_id = it.get("cluster_id")
        if isinstance(raw_id, str) and "/" in raw_id:
            parts = raw_id.split("/", 1)
            layer = layer or parts[0]
            raw_id = parts[1]
        try:
            cid = int(raw_id)
        except (TypeError, ValueError):
            continue
        if not layer:
            continue
        typical = it.get("typical_section_cluster_ids") or it.get("typical_sections") or []
        typical_ids: List[int] = []
        if isinstance(typical, list):
            for x in typical:
                try:
                    typical_ids.append(int(x))
                except (TypeError, ValueError):
                    continue
        out[(layer, cid)] = {
            "style_name": str(it.get("style_name") or it.get("display_name") or f"style_{cid}"),
            "description": str(it.get("description") or ""),
            "display_name": str(it.get("display_name") or it.get("style_name") or ""),
            "typical_section_cluster_ids": typical_ids,
            "caption_type": str(it.get("caption_type") or "").strip(),
            "header_semantics": str(it.get("header_semantics") or "").strip(),
        }
    return out


def induce_catalog_from_all_layers(
    *,
    clusters_by_layer: Dict[Layer, List[List[DocElement]]],
    language_model,
    prompt: Dict[str, str],
    llm_kwargs: Dict[str, Any],
    requirement: str = "",
    max_cluster_examples: int = 3,
    max_content_chars: int = 800,
    include_props: bool = True,
) -> Tuple[Catalog, Dict[str, Dict[str, Any]]]:
    """Name every template cluster in one LLM call. Cluster ids are per-object."""
    catalog = Catalog()
    declarations: Dict[str, Dict[str, Any]] = {}
    order: List[Layer] = [ly for ly in LAYER_ORDER if clusters_by_layer.get(ly)]
    if not order:
        return catalog, declarations

    section_map = _section_loc_to_cluster(clusters_by_layer.get("section") or [])
    layers_payload: List[Dict[str, Any]] = []
    reps_by_layer: Dict[Layer, List[Tuple[int, DocElement, List[DocElement]]]] = {}
    for layer in order:
        clusters = clusters_by_layer.get(layer) or []
        reps: List[Tuple[int, DocElement, List[DocElement]]] = []
        items: List[Dict[str, Any]] = []
        for i, cluster in enumerate(clusters, start=1):
            if not cluster:
                continue
            rep = sorted(cluster, key=lambda e: str(e.location_id))[0]
            reps.append((i, rep, cluster))
            item: Dict[str, Any] = {
                "cluster_id": i,
                "size": len(cluster),
                "content": _cluster_content(
                    cluster,
                    max_examples=max_cluster_examples,
                    max_chars=max_content_chars,
                ),
            }
            if include_props:
                item["format_properties"] = _format_properties(layer, rep)
            if layer == "table":
                item["section_cluster_ids"] = _table_section_cluster_ids(cluster, section_map)
                usage = cluster_table_usage(cluster)
                if usage["captions"]:
                    item["captions"] = usage["captions"]
                if usage["header_rows"]:
                    item["header_rows"] = usage["header_rows"]
            if layer == "image":
                item["section_index"] = (rep.meta or {}).get("section_index")
            items.append(item)
        reps_by_layer[layer] = reps
        layers_payload.append({"object": layer, "clusters": items})

    user = prompt["user_template"]
    user = user.replace("{{layers_json}}", json.dumps(layers_payload, ensure_ascii=False, indent=2))
    req = (requirement or "").strip()[:18000] or "(none)"
    user = user.replace("{{requirement}}", req)
    result = language_model.chat_json(system=prompt["system"], user=user, **llm_kwargs)
    naming = _parse_joint_naming(result)
    if not naming and llm_content_text(result).strip():
        log = logger_of(language_model)
        try:
            parse_llm_json_strict(result, layer="catalog")
        except LlmJsonParseError as err:
            if log is not None:
                log.note_parse_failure(layer=err.layer, message=str(err), raw=err.raw)
            naming = {}

    used: set[str] = set()
    section_cluster_to_sid: Dict[int, str] = {}
    pending_tables: List[Tuple[CatalogEntry, List[int]]] = []
    for layer in order:
        for i, rep, cluster in reps_by_layer.get(layer) or []:
            meta = naming.get((layer, i)) or {}
            display = meta.get("display_name") or meta.get("style_name") or f"{layer}_{i}"
            style_id = slug_style_id(layer, meta.get("style_name") or display, used)
            typical_ids = list(meta.get("typical_section_cluster_ids") or [])
            usage = cluster_table_usage(cluster) if layer == "table" else {"captions": [], "header_rows": []}
            if layer == "table" and not typical_ids:
                typical_ids = _table_section_cluster_ids(cluster, section_map)
            entry = CatalogEntry(
                style_id=style_id,
                object=layer,
                display_name=display,
                description=meta.get("description") or "",
                exemplar_path=rep.path,
                exemplar_location_id=rep.location_id,
                caption_type=str(meta.get("caption_type") or ""),
                header_semantics=str(meta.get("header_semantics") or ""),
                captions=list(usage.get("captions") or []),
                header_rows=list(usage.get("header_rows") or []),
            )
            if layer == "table":
                if not entry.header_semantics and entry.header_rows:
                    entry.header_semantics = entry.header_rows[0]
                if not entry.caption_type and entry.captions:
                    entry.caption_type = entry.captions[0][:80]
            catalog.entries.append(entry)
            declarations[style_id] = _declaration_for(layer, rep)
            if layer == "section":
                section_cluster_to_sid[i] = style_id
            if layer == "table":
                pending_tables.append((entry, typical_ids))

    for entry, typical_ids in pending_tables:
        entry.typical_sections = [
            section_cluster_to_sid[cid]
            for cid in typical_ids
            if cid in section_cluster_to_sid
        ]
        spec = declarations.get(entry.style_id)
        if isinstance(spec, dict):
            _copy_table_usage(entry, spec)

    return catalog, declarations


def induce_catalog_from_clusters(
    *,
    layer: Layer,
    clusters: List[List[DocElement]],
    language_model,
    prompt: Dict[str, str],
    llm_kwargs: Dict[str, Any],
    skip_if_single: bool = True,
    include_props: bool = True,
    requirement: str = "",
    max_cluster_examples: int = 3,
    max_content_chars: int = 800,
) -> Tuple[Catalog, Dict[str, Dict[str, Any]]]:
    """Name one layer of clustered template styles (kept for tests / ablations)."""
    if skip_if_single and len(clusters) == 1:
        catalog = Catalog()
        declarations: Dict[str, Dict[str, Any]] = {}
        used: set[str] = set()
        rep = sorted(clusters[0], key=lambda e: str(e.location_id))[0]
        style_id = slug_style_id(layer, "default", used)
        catalog.entries.append(
            CatalogEntry(
                style_id=style_id,
                object=layer,
                display_name="default",
                description="single style cluster",
                exemplar_path=rep.path,
                exemplar_location_id=rep.location_id,
            )
        )
        declarations[style_id] = _declaration_for(layer, rep)
        return catalog, declarations
    return induce_catalog_from_all_layers(
        clusters_by_layer={layer: clusters},
        language_model=language_model,
        prompt=prompt,
        llm_kwargs=llm_kwargs,
        requirement=requirement,
        max_cluster_examples=max_cluster_examples,
        max_content_chars=max_content_chars,
        include_props=include_props,
    )


def catalog_from_text(
    *,
    text: str,
    language_model,
    prompt: Dict[str, str],
    llm_kwargs: Dict[str, Any],
) -> Catalog:
    user = prompt["user_template"].replace("{{text_input}}", text[:24000])
    result = language_model.chat_json(system=prompt["system"], user=user, **llm_kwargs)
    parsed = parse_llm_json(result)
    items = parsed.get("styles") or parsed.get("entries") or parsed.get("catalog") or []
    catalog = Catalog()
    used: set[str] = set()
    if not isinstance(items, list):
        return catalog
    for it in items:
        if not isinstance(it, dict):
            continue
        layer = str(it.get("object") or it.get("layer") or "paragraph.body")
        name = str(it.get("style_name") or it.get("display_name") or it.get("style_id") or "style")
        sid = str(it.get("style_id") or "").strip() or slug_style_id(layer, name, used)  # type: ignore[arg-type]
        if sid in used:
            sid = slug_style_id(layer, name, used)  # type: ignore[arg-type]
        used.add(sid)
        catalog.entries.append(
            CatalogEntry(
                style_id=sid,
                object=layer,  # type: ignore[arg-type]
                display_name=str(it.get("display_name") or name),
                description=str(it.get("description") or ""),
            )
        )
    return catalog
