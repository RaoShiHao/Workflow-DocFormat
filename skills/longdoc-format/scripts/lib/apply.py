"""Skill-facing apply: validate T/A/M, then the shared apply_core engine."""

from __future__ import annotations

import signal
from pathlib import Path
from typing import Any

from lib.apply_core import DEFAULT_CHUNK, apply_document
from lib.whitelist import CELL_KEYS, HEADER_FOOTER_KEYS, TABLE_FORMAT_KEYS, keys_for

DEFAULT_BUDGET = 0.0  # unused; kept so older callers importing the name don't break


def _bag(spec: dict | None, layer: str) -> dict[str, Any]:
    spec = spec or {}
    if layer == "table":
        return {}
    if isinstance(spec.get("props"), dict):
        return dict(spec["props"])
    return {k: v for k, v in spec.items() if k != "object"}


def validate(
    catalog_entries: list[dict],
    props: dict[str, Any],
    loc: dict[str, Any],
    inventory: dict[str, list[dict]],
) -> list[str]:
    errors: list[str] = []
    ids = {str(e.get("style_id")) for e in catalog_entries if e.get("style_id")}
    objects = {str(e.get("style_id")): str(e.get("object") or "") for e in catalog_entries}
    if not ids:
        errors.append("target_set.entries is empty")
    for sid, spec in (props or {}).items():
        if sid not in ids:
            errors.append(f"target_props unknown style_id: {sid}")
            continue
        if not isinstance(spec, dict):
            errors.append(f"target_props.{sid} must be an object")
            continue
        layer = str(spec.get("object") or objects.get(sid) or "")
        if layer == "table":
            tf = spec.get("table_format") if isinstance(spec.get("table_format"), dict) else {}
            for k in tf:
                if k not in TABLE_FORMAT_KEYS:
                    errors.append(f"target_props.{sid}.table_format extra key: {k}")
            cells = spec.get("cells") if isinstance(spec.get("cells"), dict) else {}
            for slot, chrome in cells.items():
                if not isinstance(chrome, dict):
                    continue
                for k in chrome:
                    if k not in CELL_KEYS:
                        errors.append(f"target_props.{sid}.cells.{slot} extra key: {k}")
        elif layer == "section":
            bag = _bag(spec, layer)
            for k in bag:
                if str(k).startswith("_"):
                    continue
                if k not in keys_for("section") and k not in HEADER_FOOTER_KEYS:
                    errors.append(f"target_props.{sid} extra key: {k}")
        else:
            allowed = set(keys_for(layer))
            bag = _bag(spec, layer)
            for k in bag:
                if str(k).startswith("_"):
                    continue
                if allowed and k not in allowed:
                    errors.append(f"target_props.{sid} extra key: {k}")
        if layer == "table":
            cell_paras = spec.get("cell_paragraphs") if isinstance(spec.get("cell_paragraphs"), dict) else {}
            for slot, para_sid in cell_paras.items():
                if str(para_sid).strip() and str(para_sid) not in ids:
                    errors.append(f"target_props.{sid}.cell_paragraphs.{slot} → unknown style_id {para_sid}")
    loc_ok = {(layer, str(r.get("location_id"))) for layer, rows in inventory.items() for r in rows}
    by_layer = loc.get("by_layer") or {}
    if not any(isinstance(m, dict) and m for m in by_layer.values()) and not loc.get("table_cells") and not loc.get("paragraph_runs"):
        errors.append("target_loc is empty")
    for layer, mapping in by_layer.items():
        if str(layer) in {"paragraph.table_cell", "run"}:
            continue
        if not isinstance(mapping, dict):
            errors.append(f"target_loc.by_layer.{layer} must be an object")
            continue
        for loc_id, sid in mapping.items():
            if str(sid) not in ids:
                errors.append(f"target_loc {layer}[{loc_id}] → unknown style_id {sid}")
            if (str(layer), str(loc_id)) not in loc_ok:
                errors.append(f"target_loc {layer}[{loc_id}] not in source inventory")
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
