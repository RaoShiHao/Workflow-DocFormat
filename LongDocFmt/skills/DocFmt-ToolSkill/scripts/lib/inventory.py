"""Docx inventory for the skill scripts.

Uses the skill-local read engine in ``lib.read_engine`` (same query/batch
logic as the LongDocFmt workflow). ``profile=assign`` for source/init;
``profile=full`` for template clustering. No repo PYTHONPATH required.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from lib.cluster import header_row_of, pick_caption_of
from lib.officecli import close_doc, open_doc
from lib.read_engine import inventory

INVENTORY_SCHEMA = 5
PREVIEW_LAYERS = ("section", "paragraph.body", "table", "image")
COMPACT_CHARS = 48

_LOCK = threading.RLock()


def _live_elements(doc: Path, *, profile: str):
    with _LOCK:
        open_doc(doc)
        try:
            return inventory(doc, profile=profile)
        finally:
            close_doc(doc)


def dump_elements(by_layer: dict[str, list]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for layer, els in (by_layer or {}).items():
        rows = []
        for el in els or []:
            rows.append(el.to_dict() if hasattr(el, "to_dict") else dict(el))
        out[str(layer)] = rows
    return out


def read_docx(doc: Path, *, profile: str = "assign", include_runs: bool = True) -> dict[str, list[dict]]:
    """Return by_layer dicts (DocElement.to_dict). ``include_runs`` is kept for CLI compat; delta runs are always collected."""
    del include_runs
    mode = "full" if str(profile).strip().lower() == "full" else "assign"
    elements = _live_elements(Path(doc), profile=mode)
    return dump_elements(elements)


def dump_inventory(by_layer: dict[str, list[dict]]) -> dict[str, Any]:
    payload: dict[str, Any] = {"_schema": INVENTORY_SCHEMA}
    for layer, rows in (by_layer or {}).items():
        payload[str(layer)] = rows
    if "_profile" not in payload:
        payload["_profile"] = "assign"
    return payload


def load_inventory(data: dict[str, Any]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for layer, rows in (data or {}).items():
        if str(layer).startswith("_"):
            continue
        if isinstance(rows, list):
            out[str(layer)] = [r for r in rows if isinstance(r, dict)]
    return out


def preview(
    by_layer: dict[str, list[dict]],
    *,
    content_chars: int = 240,
    layers: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Slim view for the model: required layers only."""
    want = layers or PREVIEW_LAYERS
    layers_map = load_inventory(by_layer) if any(str(k).startswith("_") for k in (by_layer or {})) else by_layer
    out: dict[str, Any] = {
        "_counts": {k: len(v or []) for k, v in (layers_map or {}).items() if not str(k).startswith("_")},
        "_preview_layers": list(want),
        "_map_these": list(want),
        "_do_not_map": ["paragraph.table_cell", "run"],
        "_note": (
            "Map only _map_these (section, paragraph.body, table, image). "
            "Skip empty body paragraphs. Do not map paragraph.table_cell or run. "
            "Tables: hop 1 caption + header_row → Tbl*; hop 2 write table_cells.cell_style from cell text. "
            "header=column titles, stub=row titles; both may share one Para*Cell. "
            "Fonts come from that Tbl* cell_paragraphs[slot]. Do not write paragraph_style."
        ),
        "_profile": (by_layer or {}).get("_profile") or "assign",
    }
    for layer in want:
        rows = layers_map.get(layer) or []
        slim = []
        for r in rows:
            text = str(r.get("content") or "").replace("\n", " ").strip()
            if len(text) > content_chars:
                text = text[:content_chars] + "…"
            item: dict[str, Any] = {
                "location_id": r.get("location_id"),
                "path": r.get("path"),
                "content": text,
            }
            outline = (r.get("props") or {}).get("outlineLvl") or (r.get("meta") or {}).get("outlineLvl")
            if outline not in (None, "", "none"):
                item["outlineLvl"] = outline
            if layer == "table":
                cells = (r.get("meta") or {}).get("cells") or []
                item["n_cells"] = len(cells)
                meta = r.get("meta") or {}
                if meta.get("n_rows"):
                    item["n_rows"] = meta.get("n_rows")
                if meta.get("n_cols"):
                    item["n_cols"] = meta.get("n_cols")
                if meta.get("section_index") not in (None, ""):
                    item["section_index"] = meta.get("section_index")
                caption = pick_caption_of(r)
                hdr = header_row_of(r)
                if caption:
                    item["caption"] = caption[: min(160, content_chars)]
                if hdr:
                    item["header_row"] = hdr
                before, after = _neighbor_clips(meta, n=2, chars=min(120, content_chars))
                if before:
                    item["before"] = before
                if after:
                    item["after"] = after
                cell_clips = _cell_clips(meta, chars=min(120, content_chars))
                if cell_clips:
                    item["cells"] = cell_clips
            slim.append(item)
        out[layer] = slim
    return out


def _cell_clips(meta: dict, *, chars: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for cell in (meta or {}).get("cells") or []:
        if not isinstance(cell, dict):
            continue
        row, col = cell.get("row"), cell.get("col")
        if row is None or col is None:
            continue
        out.append({"row": row, "col": col, "text": _clip(cell.get("text"), chars)})
    return out


def _clip(text: Any, n: int) -> str:
    s = str(text or "").replace("\n", " ").strip()
    if len(s) > n:
        return s[:n] + "…"
    return s


def _neighbor_clips(meta: dict, *, n: int, chars: int) -> tuple[list[dict], list[dict]]:
    def _take(rows: Any, *, tail: bool) -> list[dict]:
        out: list[dict] = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            text = str(row.get("content") or "").strip()
            if not text:
                continue
            out.append({"id": row.get("location_id"), "text": _clip(text, chars)})
        return out[-n:] if tail else out[:n]

    return _take(meta.get("neighbor_before"), tail=True), _take(meta.get("neighbor_after"), tail=False)


def compact_preview(
    by_layer: dict[str, list[dict]],
    *,
    content_chars: int = COMPACT_CHARS,
    layers: tuple[str, ...] | None = None,
    layer_filter: str | None = None,
) -> dict[str, Any]:
    """Tiny id list for cat. Use this instead of preview.json when the host truncates stdout."""
    want = layers or PREVIEW_LAYERS
    layers_map = load_inventory(by_layer) if any(str(k).startswith("_") for k in (by_layer or {})) else by_layer
    out: dict[str, Any] = {
        "_map_these": list(want),
        "_do_not_map": ["paragraph.table_cell", "run"],
        "_note": (
            "Do not cat this full file. Use the per-layer files listed in "
            "preview.ids.manifest.json (one whole layer per file). "
            "Map that layer's ids → stylesheet style_id. Skip empty body paragraphs. "
            "Do not map cell paragraphs or run. "
            "Tables: hop 1 caption + header_row → Tbl*; hop 2 write table_cells.cell_style from cells[]. "
            "header=column titles, stub=row titles; fonts from cell_paragraphs[slot]. "
            "outlineLvl is a residual cue when present; do not assume a heading outline. "
            "section_index / Tbl*.typical_sections are auxiliary."
        ),
    }
    for layer in want:
        if layer_filter and str(layer) != str(layer_filter):
            continue
        rows = []
        for r in layers_map.get(layer) or []:
            loc = str(r.get("location_id") if r.get("location_id") is not None else "")
            item: dict[str, Any] = {"id": loc, "text": _clip(r.get("content"), content_chars)}
            outline = (r.get("props") or {}).get("outlineLvl") or (r.get("meta") or {}).get("outlineLvl")
            if outline not in (None, "", "none"):
                item["outlineLvl"] = outline
            if layer == "table":
                meta = r.get("meta") or {}
                item["n_rows"] = meta.get("n_rows") or 0
                item["n_cols"] = meta.get("n_cols") or 0
                if meta.get("section_index") not in (None, ""):
                    item["section_index"] = meta.get("section_index")
                caption = pick_caption_of(r)
                hdr = header_row_of(r)
                if caption:
                    item["caption"] = _clip(caption, content_chars)
                if hdr:
                    item["header_row"] = [_clip(h, content_chars) for h in hdr]
                before, after = _neighbor_clips(meta, n=2, chars=content_chars)
                if before:
                    item["before"] = before
                if after:
                    item["after"] = after
                cell_clips = _cell_clips(meta, chars=content_chars)
                if cell_clips:
                    item["cells"] = cell_clips
            props = r.get("props") if isinstance(r.get("props"), dict) else {}
            if layer == "section":
                for k in ("pageNumFmt", "titlePage", "orientation"):
                    if props.get(k) not in (None, "", "none"):
                        item[k] = props.get(k)
            if layer == "image":
                for k in ("width", "height"):
                    if props.get(k) not in (None, "", "none"):
                        item[k] = props.get(k)
            rows.append(item)
        out[layer] = rows
    return out


def slice_preview(
    preview_data: dict[str, Any],
    *,
    content_chars: int = COMPACT_CHARS,
    layer_filter: str | None = None,
) -> dict[str, Any]:
    """Rebuild one whole-layer compact list from preview.json (no re-parse)."""
    out: dict[str, Any] = {
        "_map_these": list(preview_data.get("_map_these") or PREVIEW_LAYERS),
        "_do_not_map": list(preview_data.get("_do_not_map") or ["paragraph.table_cell", "run"]),
    }
    for layer in out["_map_these"]:
        if layer_filter and str(layer) != str(layer_filter):
            continue
        rows = []
        for r in preview_data.get(layer) or []:
            if not isinstance(r, dict):
                continue
            loc = str(r.get("location_id") if r.get("location_id") is not None else r.get("id") or "")
            item: dict[str, Any] = {"id": loc, "text": _clip(r.get("content") or r.get("text"), content_chars)}
            if r.get("outlineLvl") not in (None, "", "none"):
                item["outlineLvl"] = r.get("outlineLvl")
            if layer == "table":
                if r.get("n_rows") is not None:
                    item["n_rows"] = r.get("n_rows")
                if r.get("n_cols") is not None:
                    item["n_cols"] = r.get("n_cols")
                if r.get("n_cells") is not None:
                    item["n_cells"] = r.get("n_cells")
                if r.get("section_index") not in (None, ""):
                    item["section_index"] = r.get("section_index")
                if r.get("caption"):
                    item["caption"] = _clip(r.get("caption"), content_chars)
                if r.get("header_row"):
                    hdr = r.get("header_row")
                    if isinstance(hdr, list):
                        item["header_row"] = [_clip(h, content_chars) for h in hdr]
                    else:
                        item["header_row"] = hdr
                if r.get("before"):
                    item["before"] = r.get("before")
                if r.get("after"):
                    item["after"] = r.get("after")
                cells = r.get("cells") if isinstance(r.get("cells"), list) else []
                if cells:
                    item["cells"] = [
                        {
                            "row": c.get("row"),
                            "col": c.get("col"),
                            "text": _clip(c.get("text"), content_chars),
                        }
                        for c in cells
                        if isinstance(c, dict) and c.get("row") is not None and c.get("col") is not None
                    ]
            rows.append(item)
        out[layer] = rows
    return out


def _layer_payload(layer: str, rows: list[dict[str, Any]], *, note: str) -> dict[str, Any]:
    return {
        "_map_these": [layer],
        "_do_not_map": ["paragraph.table_cell", "run"],
        "_note": note,
        layer: rows,
    }


def _table_hop1_row(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k != "cells"}


def write_layer_files(
    compact: dict[str, Any],
    dest_dir: Path,
    *,
    stem: str = "preview.ids",
) -> dict[str, Any]:
    """Write one whole-layer file per workflow hop. Do not split a layer."""
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    read_order: list[dict[str, Any]] = []

    def _write(name: str, payload: dict[str, Any]) -> Path:
        path = dest / name
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def _emit(layer: str, *, note: str) -> None:
        rows = [r for r in (compact.get(layer) or []) if isinstance(r, dict)]
        if not rows:
            return
        name = f"{stem}.{layer}.json"
        _write(name, _layer_payload(layer, rows, note=note))
        read_order.append({"file": name, "layer": layer, "n": len(rows)})

    _emit(
        "section",
        note="Merge entire section via merge_loc.py --layer-loc with by_layer.section {id: style_id}. Do not cat the full ids file.",
    )
    _emit(
        "paragraph.body",
        note="Merge entire paragraph.body via --layer-loc with by_layer['paragraph.body'] {id: style_id} (no chat narration). Skip empty text. Do not split this file.",
    )

    tables = [r for r in (compact.get("table") or []) if isinstance(r, dict)]
    if tables:
        hop1 = [_table_hop1_row(r) for r in tables]
        name = f"{stem}.table.json"
        _write(
            name,
            _layer_payload(
                "table",
                hop1,
                note="Hop 1 only: merge --layer-loc with by_layer.table {id: Tbl*}. Bind from caption + header_row. No cells here.",
            ),
        )
        read_order.append({"file": name, "layer": "table", "hop": 1, "n": len(hop1)})
        name = f"{stem}.table.cells.json"
        _write(
            name,
            _layer_payload(
                "table",
                tables,
                note="Hop 2: merge --layer-loc with table_cells {table_id: [{row, col, cell_style}, ...]} for every physical cell.",
            ),
        )
        read_order.append({"file": name, "layer": "table", "hop": 2, "n": len(tables)})

    _emit(
        "image",
        note="Merge entire image via --layer-loc with by_layer.image {id: style_id}.",
    )

    manifest = {
        "do_not_cat": [f"{stem}.json", "preview.json", "inventory.json", "stylesheet.json"],
        "read_order": read_order,
        "_note": (
            "Follow read_order. Each file is one whole workflow layer (or one table hop). "
            "Merge that layer with merge_loc.py --layer-loc (one-layer target_loc JSON; do not narrate in chat). "
            "Do not split a layer. Do not cat the full ids file."
        ),
    }
    man_name = f"{stem}.manifest.json"
    _write(man_name, manifest)
    manifest["manifest"] = man_name
    return manifest


def write_id_slices(
    compact: dict[str, Any],
    dest_dir: Path,
    *,
    stem: str = "preview.ids",
    **_unused: Any,
) -> dict[str, Any]:
    """Backward-compatible name for write_layer_files. Ignores slice kwargs."""
    return write_layer_files(compact, dest_dir, stem=stem)


def merge_target_loc(base: dict[str, Any] | None, layer_payload: dict[str, Any] | None) -> dict[str, Any]:
    """Merge one layer mapping into target_loc.json."""
    out: dict[str, Any] = {
        "by_layer": {
            "section": {},
            "paragraph.body": {},
            "table": {},
            "image": {},
            "run": {},
            "paragraph.table_cell": {},
        },
        "table_cells": {},
        "paragraph_runs": {},
    }
    if isinstance(base, dict):
        raw_layers = base.get("by_layer") if isinstance(base.get("by_layer"), dict) else {}
        for layer, mapping in raw_layers.items():
            if isinstance(mapping, dict):
                out["by_layer"].setdefault(str(layer), {}).update({str(k): v for k, v in mapping.items()})
        cells = base.get("table_cells") if isinstance(base.get("table_cells"), dict) else {}
        for tid, rows in cells.items():
            if isinstance(rows, list):
                out["table_cells"][str(tid)] = list(rows)
        runs = base.get("paragraph_runs") if isinstance(base.get("paragraph_runs"), dict) else {}
        if runs:
            out["paragraph_runs"].update(runs)
    if not isinstance(layer_payload, dict):
        return out
    raw_layers = layer_payload.get("by_layer") if isinstance(layer_payload.get("by_layer"), dict) else {}
    for layer, mapping in raw_layers.items():
        if isinstance(mapping, dict):
            out["by_layer"].setdefault(str(layer), {}).update({str(k): v for k, v in mapping.items()})
    cells = layer_payload.get("table_cells") if isinstance(layer_payload.get("table_cells"), dict) else {}
    for tid, rows in cells.items():
        if isinstance(rows, list):
            out["table_cells"][str(tid)] = list(rows)
    runs = layer_payload.get("paragraph_runs") if isinstance(layer_payload.get("paragraph_runs"), dict) else {}
    if runs:
        out["paragraph_runs"].update(runs)
    return out
