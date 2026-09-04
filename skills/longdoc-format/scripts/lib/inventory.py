"""Docx inventory for the skill scripts.

Uses the skill-local read engine in ``lib.read_engine`` (same query/batch
logic as the LongDocFormatter workflow). ``profile=assign`` for source/init;
``profile=full`` for template clustering. No repo PYTHONPATH required.
"""
from __future__ import annotations

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
            "Map only _map_these. Leave paragraph.table_cell empty in M; "
            "apply copies cell fonts from the Tbl* role's cell_paragraphs."
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
            slim.append(item)
        out[layer] = slim
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
    from_id: str | None = None,
    to_id: str | None = None,
    layer_filter: str | None = None,
) -> dict[str, Any]:
    """Tiny id list for cat. Use this instead of preview.json when the host truncates stdout."""
    want = layers or PREVIEW_LAYERS
    layers_map = load_inventory(by_layer) if any(str(k).startswith("_") for k in (by_layer or {})) else by_layer
    out: dict[str, Any] = {
        "_map_these": list(want),
        "_do_not_map": ["paragraph.table_cell", "run"],
        "_note": (
            "Map these ids → stylesheet style_id. Do not map cell paragraphs; "
            "Tbl* cell_paragraphs covers cell fonts. outlineLvl beats title-looking text. "
            "Tables: match source caption + header_row to Tbl* captions / header_rows. "
            "section_index / Tbl*.typical_sections are auxiliary."
        ),
    }
    for layer in want:
        if layer_filter and str(layer) != str(layer_filter):
            continue
        rows = []
        for r in layers_map.get(layer) or []:
            loc = str(r.get("location_id") if r.get("location_id") is not None else "")
            if from_id and _id_sort_key(loc) < _id_sort_key(str(from_id)):
                continue
            if to_id and _id_sort_key(loc) > _id_sort_key(str(to_id)):
                continue
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


def _id_sort_key(loc: str) -> tuple:
    try:
        return (0, int(loc))
    except (TypeError, ValueError):
        return (1, str(loc))


def slice_preview(
    preview_data: dict[str, Any],
    *,
    content_chars: int = COMPACT_CHARS,
    from_id: str | None = None,
    to_id: str | None = None,
    layer_filter: str | None = None,
) -> dict[str, Any]:
    """Rebuild a compact id list from an existing preview.json (no re-parse)."""
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
            if from_id and _id_sort_key(loc) < _id_sort_key(str(from_id)):
                continue
            if to_id and _id_sort_key(loc) > _id_sort_key(str(to_id)):
                continue
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
            rows.append(item)
        out[layer] = rows
    return out
