"""Deterministic format overlay: sparse patch onto a base attribute map.

This is a composable pipeline primitive (no LLM). Planner recipes may chain
``from_exemplars`` → ``from_text`` (sparse) → ``overlay`` without bespoke routing.
"""
from __future__ import annotations

from typing import Any, Dict

from LongDocFormatter.workflow.whitelist import CELL_KEYS, HEADER_FOOTER_KEYS


def overlay_target_attributes(
    base: Dict[str, Dict[str, Any]],
    patch: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """Merge ``patch`` onto ``base``; only keys present in patch replace base values."""
    out = {k: dict(v) for k, v in (base or {}).items()}
    for sid, spec in (patch or {}).items():
        if not isinstance(spec, dict):
            continue
        if sid not in out:
            out[sid] = dict(spec)
            continue
        base_obj = str(out[sid].get("object") or "")
        over_obj = str(spec.get("object") or "")
        if base_obj and over_obj and base_obj != over_obj:
            continue
        out[sid] = _merge_one_target(out[sid], spec)
    return out


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


def _normalize_cell_chrome(chrome: Dict[str, Any] | None) -> Dict[str, Any]:
    return {
        k: v
        for k, v in (chrome or {}).items()
        if k in CELL_KEYS and v not in (None, "")
    }


def _merge_shallow_dicts(base: Dict[str, Any] | None, overlay: Dict[str, Any] | None) -> Dict[str, Any]:
    out = dict(base or {})
    for k, v in (overlay or {}).items():
        if k in {"style_id", "object", "display_name", "description"}:
            continue
        if v is not None:
            out[k] = v
    return out


def _merge_cell_slots(
    base_cells: Dict[str, Any] | None,
    overlay_cells: Dict[str, Any] | None,
) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for name, chrome in (base_cells or {}).items():
        if isinstance(chrome, dict):
            out[str(name)] = dict(chrome)
    for name, chrome in (overlay_cells or {}).items():
        if not isinstance(chrome, dict):
            continue
        slot = str(name)
        merged = dict(out.get(slot) or {})
        merged.update(_normalize_cell_chrome(chrome))
        if merged:
            out[slot] = merged
    return out


def _merge_one_target(base_spec: Dict[str, Any], overlay_spec: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base_spec)
    obj = str(overlay_spec.get("object") or merged.get("object") or "")
    if obj:
        merged["object"] = obj

    if obj == "table" or merged.get("table_format") is not None or merged.get("cells") is not None:
        merged["object"] = "table"
        merged["table_format"] = _merge_shallow_dicts(
            merged.get("table_format") if isinstance(merged.get("table_format"), dict) else {},
            overlay_spec.get("table_format") if isinstance(overlay_spec.get("table_format"), dict) else {},
        )
        merged["cells"] = _merge_cell_slots(
            merged.get("cells") if isinstance(merged.get("cells"), dict) else {},
            overlay_spec.get("cells") if isinstance(overlay_spec.get("cells"), dict) else {},
        )
        return merged

    if obj == "section" or merged.get("object") == "section":
        merged["object"] = "section"
        base_props = merged.get("props") if isinstance(merged.get("props"), dict) else {}
        over_props = overlay_spec.get("props") if isinstance(overlay_spec.get("props"), dict) else {}
        merged["props"] = _merge_shallow_dicts(base_props, over_props)
        for k in ("header", "footer", "header_first"):
            if k in overlay_spec and isinstance(overlay_spec.get(k), dict):
                merged[k] = _merge_shallow_dicts(
                    merged.get(k) if isinstance(merged.get(k), dict) else {},
                    _normalize_hf(overlay_spec.get(k)),
                )
        return merged

    base_props = merged.get("props") if isinstance(merged.get("props"), dict) else merged
    if not isinstance(base_props, dict):
        base_props = {}
    over_props = overlay_spec.get("props") if isinstance(overlay_spec.get("props"), dict) else overlay_spec
    if not isinstance(over_props, dict):
        over_props = {}
    return {
        "object": obj or str(merged.get("object") or ""),
        "props": _merge_shallow_dicts(base_props, over_props),
    }


# Backward-compatible alias
merge_attribute_dicts = overlay_target_attributes
