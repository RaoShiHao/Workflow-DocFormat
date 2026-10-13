"""Skill-facing apply: sanitize T/A/M (drop illegal keys), then apply_core."""

from __future__ import annotations

import copy
import signal
from pathlib import Path
from typing import Any

from lib.apply_core import DEFAULT_CHUNK, apply_document
from lib.whitelist import CELL_KEYS, HEADER_FOOTER_KEYS, TABLE_FORMAT_KEYS, keys_for

DEFAULT_BUDGET = 0.0  # unused; kept so older callers importing the name don't break

# Nested section chrome containers (apply_core reads these on the style spec).
_SECTION_HF = ("header", "footer", "header_first")


def _bag(spec: dict | None, layer: str) -> dict[str, Any]:
    spec = spec or {}
    if layer == "table":
        return {}
    if isinstance(spec.get("props"), dict):
        return dict(spec["props"])
    return {k: v for k, v in spec.items() if k != "object"}


def _sanitize_hf(chrome: Any) -> dict[str, Any] | None:
    if not isinstance(chrome, dict):
        return None
    out = {k: v for k, v in chrome.items() if k in HEADER_FOOTER_KEYS}
    return out or None


def _sanitize_props(catalog_entries: list[dict], props: dict[str, Any]) -> dict[str, Any]:
    """Drop unknown style_ids and illegal keys. Do not report drops."""
    ids = {str(e.get("style_id")) for e in catalog_entries if e.get("style_id")}
    objects = {str(e.get("style_id")): str(e.get("object") or "") for e in catalog_entries}
    clean: dict[str, Any] = {}
    for sid, spec in (props or {}).items():
        sid = str(sid)
        if sid not in ids or not isinstance(spec, dict):
            continue
        spec = copy.deepcopy(spec)
        layer = str(spec.get("object") or objects.get(sid) or "")
        if layer == "table":
            tf = spec.get("table_format") if isinstance(spec.get("table_format"), dict) else {}
            spec["table_format"] = {k: v for k, v in tf.items() if k in TABLE_FORMAT_KEYS}
            cells_in = spec.get("cells") if isinstance(spec.get("cells"), dict) else {}
            cells_out: dict[str, Any] = {}
            for slot, chrome in cells_in.items():
                if isinstance(chrome, dict):
                    filtered = {k: v for k, v in chrome.items() if k in CELL_KEYS}
                    if filtered:
                        cells_out[str(slot)] = filtered
            spec["cells"] = cells_out
            cell_paras = spec.get("cell_paragraphs") if isinstance(spec.get("cell_paragraphs"), dict) else {}
            spec["cell_paragraphs"] = {
                str(slot): para_sid
                for slot, para_sid in cell_paras.items()
                if str(para_sid).strip() and str(para_sid) in ids
            }
        elif layer == "section":
            # Lift header/footer out of props so apply_core sees them on the spec.
            bag = _bag(spec, layer)
            for hf in _SECTION_HF:
                nested = bag.pop(hf, None) if hf in bag else None
                top = spec.get(hf)
                chosen = top if isinstance(top, dict) else nested
                sanitized = _sanitize_hf(chosen)
                if sanitized:
                    spec[hf] = sanitized
                elif hf in spec:
                    del spec[hf]
            allowed = set(keys_for("section"))
            filtered_bag = {
                k: v
                for k, v in bag.items()
                if (k in allowed or str(k).startswith("_")) and k not in _SECTION_HF
            }
            if "props" in spec and isinstance(spec.get("props"), dict):
                spec["props"] = filtered_bag
            else:
                keep = {"object"} | set(_SECTION_HF)
                for k in list(spec.keys()):
                    if k in keep or str(k).startswith("_"):
                        continue
                    if k not in allowed:
                        del spec[k]
                for k, v in filtered_bag.items():
                    if k not in spec:
                        spec[k] = v
        else:
            allowed = set(keys_for(layer))
            bag = _bag(spec, layer)
            filtered_bag = {
                k: v
                for k, v in bag.items()
                if (not allowed) or k in allowed or str(k).startswith("_")
            }
            if "props" in spec and isinstance(spec.get("props"), dict):
                spec["props"] = filtered_bag
            else:
                for k in list(spec.keys()):
                    if k in {"object"} or str(k).startswith("_"):
                        continue
                    if allowed and k not in allowed:
                        del spec[k]
                for k, v in filtered_bag.items():
                    if k not in spec:
                        spec[k] = v
        clean[sid] = spec
    return clean


def _sanitize_loc(
    catalog_entries: list[dict],
    loc: dict[str, Any],
    inventory: dict[str, list[dict]],
) -> dict[str, Any]:
    """Drop unknown style_ids and inventory-missing loc ids. Do not report drops."""
    ids = {str(e.get("style_id")) for e in catalog_entries if e.get("style_id")}
    loc_ok = {(layer, str(r.get("location_id"))) for layer, rows in inventory.items() for r in rows}
    out = copy.deepcopy(loc) if isinstance(loc, dict) else {"by_layer": {}}
    by_layer_in = out.get("by_layer") if isinstance(out.get("by_layer"), dict) else {}
    by_layer_out: dict[str, Any] = {}
    for layer, mapping in by_layer_in.items():
        if str(layer) in {"paragraph.table_cell", "run"}:
            by_layer_out[str(layer)] = mapping if isinstance(mapping, dict) else {}
            continue
        if not isinstance(mapping, dict):
            continue
        cleaned = {
            str(loc_id): sid
            for loc_id, sid in mapping.items()
            if str(sid) in ids and (str(layer), str(loc_id)) in loc_ok
        }
        by_layer_out[str(layer)] = cleaned
    out["by_layer"] = by_layer_out
    return out


def sanitize(
    catalog_entries: list[dict],
    props: dict[str, Any],
    loc: dict[str, Any],
    inventory: dict[str, list[dict]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return cleaned props/loc with illegal keys and bad refs removed silently."""
    return (
        _sanitize_props(catalog_entries, props),
        _sanitize_loc(catalog_entries, loc, inventory),
    )


def validate(
    catalog_entries: list[dict],
    props: dict[str, Any],
    loc: dict[str, Any],
    inventory: dict[str, list[dict]],
) -> list[str]:
    """Fatal structural checks only. Extra keys are handled by ``sanitize``."""
    del inventory  # inventory membership already enforced in sanitize
    errors: list[str] = []
    ids = {str(e.get("style_id")) for e in catalog_entries if e.get("style_id")}
    if not ids:
        errors.append("target_set.entries is empty")
    for sid, spec in (props or {}).items():
        if not isinstance(spec, dict):
            errors.append(f"target_props.{sid} must be an object")
    by_layer = loc.get("by_layer") or {}
    if not isinstance(by_layer, dict):
        errors.append("target_loc.by_layer must be an object")
        by_layer = {}
    for layer, mapping in by_layer.items():
        if str(layer) in {"paragraph.table_cell", "run"}:
            continue
        if mapping is not None and not isinstance(mapping, dict):
            errors.append(f"target_loc.by_layer.{layer} must be an object")
    if not any(isinstance(m, dict) and m for m in by_layer.values()) and not loc.get("table_cells") and not loc.get("paragraph_runs"):
        errors.append("target_loc is empty")
    return errors


def apply_ir(
    *,
    source: Path,
    output: Path,
    catalog_entries: list[dict],
    props: dict[str, Any],
    loc: dict[str, Any],
    inventory: dict[str, list[dict]],
    chunk_size: int = DEFAULT_CHUNK,
    dump_ops: Path | None = None,
    budget_seconds: float | None = None,  # ignored; close-once is the flush policy
) -> dict[str, Any]:
    _install_flush(output)
    return apply_document(
        source=source,
        output=output,
        catalog_entries=catalog_entries,
        props=props,
        loc=loc,
        inventory=inventory,
        chunk_size=chunk_size,
        dump_ops=dump_ops,
    )


def _install_flush(doc: Path) -> None:
    """On SIGTERM, close so resident edits reach disk (DSH bash 120s)."""
    from lib.officecli import close_doc, save_doc

    def _flush(signum: int, _frame: Any) -> None:
        try:
            save_doc(doc)
            close_doc(doc)
        finally:
            raise SystemExit(128 + int(signum))

    for name in ("SIGTERM", "SIGINT"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _flush)
        except (ValueError, OSError):
            continue
