"""Cluster template elements that share the same whitelist formatting signature."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from lib.cell_plan import infer_table_style, table_role_signature
from lib.whitelist import HEADER_FOOTER_KEYS, TABLE_FORMAT_KEYS, filter_props, keys_for

_PREFIX = {
    "section": "Sec",
    "paragraph.body": "Para",
    "paragraph.table_cell": "Para",
    "table": "Tbl",
    "image": "Img",
    "run": "Run",
}

_HF_SLOTS = ("header", "footer", "header_first")
_LAYERS = (
    "section",
    "paragraph.body",
    "paragraph.table_cell",
    "table",
    "image",
)


def _hf_of(row: dict[str, Any]) -> dict[str, Any]:
    props = row.get("props") if isinstance(row.get("props"), dict) else {}
    meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
    hf = meta.get("header_footer") or props.get("_header_footer")
    return hf if isinstance(hf, dict) else {}


def _prune_hf_slot(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    allow = set(HEADER_FOOTER_KEYS)
    out: dict[str, Any] = {}
    for k, v in raw.items():
        if k not in allow or v in (None, "none"):
            continue
        if v == "" and k != "text":
            continue
        out[k] = v
    return out


def _hf_fingerprint(hf: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for slot in _HF_SLOTS:
        pruned = _prune_hf_slot(hf.get(slot))
        if not pruned:
            continue
        out[slot] = {k: pruned[k] for k in ("text", "field", "align", "size") if k in pruned}
    return out


_CAPTION_RE = re.compile(
    r"(?is)^\s*(?:table|tbl|tab\.?|figure|fig\.?|exhibit|附表?|表|图)\s*[.\-:]?\s*\d"
)


def _clip_text(text: Any, n: int = 160) -> str:
    s = str(text or "").replace("\n", " ").strip()
    return s[:n] if s else ""


def is_caption_text(text: str) -> bool:
    return bool(_CAPTION_RE.search(text or ""))


def _neighbor_texts(row: dict[str, Any]) -> list[str]:
    meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
    out: list[str] = []
    for key in ("neighbor_before", "neighbor_after"):
        for item in meta.get(key) or []:
            if not isinstance(item, dict):
                continue
            text = _clip_text(item.get("content") or item.get("text"))
            if text:
                out.append(text)
    return out


def header_row_of(row: dict[str, Any], *, max_cols: int = 12, max_chars: int = 80) -> list[str]:
    meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
    cells = meta.get("cells") or []
    row1: list[tuple[int, str]] = []
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


def pick_caption_of(row: dict[str, Any]) -> str:
    neighbors = _neighbor_texts(row)
    for text in neighbors:
        if is_caption_text(text):
            return text
    return neighbors[0] if neighbors else ""


def table_usage_of(row: dict[str, Any]) -> dict[str, Any]:
    captions = [t for t in _neighbor_texts(row) if is_caption_text(t)]
    if not captions:
        captions = _neighbor_texts(row)
    hdr = header_row_of(row)
    out: dict[str, Any] = {}
    if captions:
        out["captions"] = captions[:4]
    if hdr:
        out["header_row"] = hdr
        out["header_rows"] = [" | ".join(hdr)]
    cap = pick_caption_of(row)
    if cap:
        out["caption"] = cap
    return out


def _unique_texts(items: list[str], *, limit: int = 6) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.lower()
        if not item or key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def cluster_table_usage(group: list[dict]) -> dict[str, list[str]]:
    captions: list[str] = []
    header_rows: list[str] = []
    for el in group:
        usage = table_usage_of(el)
        captions.extend(usage.get("captions") or [])
        header_rows.extend(usage.get("header_rows") or [])
    return {
        "captions": _unique_texts(captions),
        "header_rows": _unique_texts(header_rows),
    }


def _sig(layer: str, row: dict[str, Any], *, cell_para_sids: dict[str, str] | None = None) -> str:
    props = dict(row.get("props") or {})
    if layer == "table":
        designed = infer_table_style(row, cell_para_sids=cell_para_sids)
        blob = table_role_signature(
            filter_props(props.get("table_format") or {}, TABLE_FORMAT_KEYS),
            designed.get("cells") or {},
            designed.get("cell_style_plan") or {},
        )
    elif layer == "section":
        blob = filter_props(props, keys_for(layer))
        fp = _hf_fingerprint(_hf_of(row))
        if fp:
            blob["_hf"] = fp
    else:
        blob = filter_props(props, keys_for(layer))
    raw = json.dumps(blob, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def cluster_layer(
    layer: str,
    rows: list[dict],
    *,
    cell_para_sids: dict[str, str] | None = None,
) -> list[list[dict]]:
    buckets: dict[str, list[dict]] = {}
    order: list[str] = []
    for row in rows:
        key = _sig(layer, row, cell_para_sids=cell_para_sids)
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(row)
    return [buckets[k] for k in order]


def _exemplar_props(layer: str, row: dict[str, Any], *, cell_para_sids: dict[str, str] | None = None) -> dict[str, Any]:
    props = dict(row.get("props") or {})
    if layer == "table":
        designed = infer_table_style(row, cell_para_sids=cell_para_sids)
        payload: dict[str, Any] = {
            "object": "table",
            "table_format": filter_props(props.get("table_format") or {}, TABLE_FORMAT_KEYS),
            "cells": designed.get("cells") or {},
            "cell_style_plan": designed.get("cell_style_plan") or {},
        }
        if designed.get("cell_paragraphs"):
            payload["cell_paragraphs"] = designed["cell_paragraphs"]
        return payload
    if layer == "section":
        payload = {"object": "section", "props": filter_props(props, keys_for(layer))}
        hf = _hf_of(row)
        for slot in _HF_SLOTS:
            pruned = _prune_hf_slot(hf.get(slot))
            if pruned:
                payload[slot] = pruned
        return payload
    return {"object": layer, "props": filter_props(props, keys_for(layer))}


def _examples(group: list[dict], *, layer: str = "", limit: int = 5) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for el in group[:limit]:
        item: dict[str, Any] = {
            "location_id": el.get("location_id"),
            "content": str(el.get("content") or "").replace("\n", " ").strip()[:200],
        }
        if layer == "table":
            usage = table_usage_of(el)
            if usage.get("captions"):
                item["captions"] = usage["captions"][:2]
            if usage.get("header_row"):
                item["header_row"] = usage["header_row"]
        out.append(item)
    return out


def build_stylesheet(by_layer: dict[str, list[dict]]) -> dict[str, Any]:
    roles: list[dict] = []
    entries: list[dict] = []
    target_props: dict[str, Any] = {}
    used: set[str] = set()
    cell_para_sids: dict[str, str] = {}
    sec_loc_to_sid: dict[str, str] = {}

    for layer in _LAYERS:
        clusters = cluster_layer(
            layer,
            by_layer.get(layer) or [],
            cell_para_sids=cell_para_sids if layer == "table" else None,
        )
        for i, group in enumerate(clusters):
            exemplar = group[0]
            prefix = _PREFIX.get(layer, "St")
            suffix = "Cell" if layer == "paragraph.table_cell" else ""
            sid = f"{prefix}Cluster{i}{suffix}"
            n = 2
            while sid in used:
                sid = f"{prefix}Cluster{i}{suffix}_{n}"
                n += 1
            used.add(sid)
            if layer == "paragraph.table_cell":
                for el in group:
                    loc = el.get("location_id")
                    if loc is not None:
                        cell_para_sids[str(loc)] = sid
            if layer == "section":
                for el in group:
                    loc = el.get("location_id")
                    if loc is not None:
                        sec_loc_to_sid[str(loc)] = sid
            typical_sections: list[str] = []
            usage: dict[str, list[str]] = {"captions": [], "header_rows": []}
            if layer == "table":
                for el in group:
                    si = (el.get("meta") or {}).get("section_index")
                    if si in (None, ""):
                        continue
                    mapped = sec_loc_to_sid.get(str(si))
                    if mapped and mapped not in typical_sections:
                        typical_sections.append(mapped)
                usage = cluster_table_usage(group)
            role = {
                "suggested_id": sid,
                "object": layer,
                "n": len(group),
                "exemplar_location_id": exemplar.get("location_id"),
                "exemplar_path": exemplar.get("path"),
                "examples": _examples(group, layer=layer),
                "member_ids": [el.get("location_id") for el in group],
            }
            if typical_sections:
                role["typical_sections"] = typical_sections
            if usage.get("captions"):
                role["captions"] = usage["captions"]
            if usage.get("header_rows"):
                role["header_rows"] = usage["header_rows"]
            roles.append(role)
            entry = {
                "style_id": sid,
                "object": layer,
                "display_name": sid,
                "description": "",
                "exemplar_path": exemplar.get("path") or "",
                "exemplar_location_id": exemplar.get("location_id"),
            }
            if typical_sections:
                entry["typical_sections"] = typical_sections
            if usage.get("captions"):
                entry["captions"] = usage["captions"]
            if usage.get("header_rows"):
                entry["header_rows"] = usage["header_rows"]
            entries.append(entry)
            target_props[sid] = _exemplar_props(layer, exemplar, cell_para_sids=cell_para_sids)

    return {
        "note": (
            "Clusters of equal formatting on the template, not Word named styles. "
            "Pass this file to apply_format.py as both --target-set and --target-props. "
            "Write only target_loc.json: SOURCE preview location_id → these style_ids. "
            "Match each source object to the cluster whose examples look like it — "
            "do not dump every section/image/empty para onto the cluster with the largest n. "
            "Tbl* is a designed table role (table_format + named cell slots + cell_style_plan on the template), "
            "not a frozen specimen grid — one Tbl* can restyle any size source table. "
            "Tbl*.typical_sections is a weak prior (which Sec* that look sits in). "
            "Hop 1: bind each source table by caption + header_row to Tbl* captions / header_rows; "
            "do not pick a grid Tbl* for a summary caption just because both sit in the same Sec*. "
            "Hop 2: write table_cells (each source cell → a slot of that Tbl*). "
            "Do not map paragraph.table_cell in M; cell fonts come from Tbl*.cell_paragraphs after you label the slot. "
            "Keep style_id keys; optional display_name from requirement text."
        ),
        "roles": roles,
        "target_set_skeleton": {"entries": entries},
        "target_props": target_props,
    }


def roles_view(stylesheet: dict[str, Any]) -> dict[str, Any]:
    """Compact sidecar for the model (cat this, not the full stylesheet)."""
    roles = []
    for r in stylesheet.get("roles") or []:
        item = {
            "style_id": r.get("suggested_id"),
            "object": r.get("object"),
            "n": r.get("n"),
            "examples": r.get("examples") or [],
        }
        if r.get("typical_sections"):
            item["typical_sections"] = r.get("typical_sections")
        if r.get("captions"):
            item["captions"] = r.get("captions")
        if r.get("header_rows"):
            item["header_rows"] = r.get("header_rows")
        members = r.get("member_ids") or []
        if len(members) <= 24:
            item["member_ids"] = members
        roles.append(item)
    return {
        "note": (
            "Map source preview ids → style_id. Same-n clusters are not interchangeable: "
            "read examples (cover vs TOC vs body; heading vs body; image sizes). "
            "Tbl* is a designed role (slots + plan on the template), not one physical template table. "
            "Match tables by usage: source caption + header_row against Tbl* captions / header_rows. "
            "typical_sections is auxiliary; the same Sec* can host open summary tables and grid matrices. "
            "Label source cells in table_cells; leave paragraph.table_cell empty in M."
        ),
        "roles": roles,
    }
