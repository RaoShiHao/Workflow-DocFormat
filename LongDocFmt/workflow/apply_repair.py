"""One-shot apply repair: patch bad styles, re-identify only failed locations.

Successful ``location_id`` values are masked. A second apply writes only the
repaired styles and locations onto the existing output.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from LongDocFmt.workflow.contracts import Assignment, Catalog, DocElement
from LongDocFmt.workflow.env_feedback import classify_tool_error

_LAYERS = ("section", "paragraph.body", "table", "image")


@dataclass
class RepairPlan:
    """What the single apply feedback pass is allowed to change."""

    attribute_style_ids: list[str] = field(default_factory=list)
    retry_ids: dict[str, list[str]] = field(default_factory=dict)
    frozen_ids: dict[str, list[str]] = field(default_factory=dict)
    dropped_fake: dict[str, list[str]] = field(default_factory=dict)
    attribute_feedback: str = ""
    location_feedback: str = ""
    attribute_failures: list[dict[str, Any]] = field(default_factory=list)

    @property
    def needs_attribute_repair(self) -> bool:
        return bool(self.attribute_style_ids)

    @property
    def needs_location_repair(self) -> bool:
        return any(self.retry_ids.values())

    @property
    def needs_apply(self) -> bool:
        return self.needs_attribute_repair or self.needs_location_repair


def _rows(elements: dict[str, Any]) -> list[tuple[str, str, str, str]]:
    rows: list[tuple[str, str, str, str]] = []
    for layer, items in (elements or {}).items():
        for el in items or []:
            if isinstance(el, DocElement):
                loc_id = el.location_id
                path = el.path
                content = el.content or ""
                layer_name = str(el.layer or layer)
            elif isinstance(el, dict):
                loc_id = el.get("location_id")
                path = el.get("path") or ""
                content = str(el.get("content") or "")
                layer_name = str(el.get("layer") or layer)
            else:
                continue
            if loc_id is None:
                continue
            rows.append((layer_name, str(loc_id), str(path or ""), content))
    return rows


def _match_path(path: str, rows: list[tuple[str, str, str, str]]) -> tuple[str, str] | None:
    target = str(path or "")
    if not target:
        return None
    best: tuple[int, str, str] | None = None
    for layer, loc_id, el_path, _content in rows:
        if not el_path:
            continue
        if target == el_path or target.startswith(el_path.rstrip("/") + "/"):
            if best is None or len(el_path) > best[0]:
                best = (len(el_path), layer, loc_id)
    if best is None:
        return None
    return best[1], best[2]


def _style_id_of(failure: dict[str, Any]) -> str:
    path = str(failure.get("path") or "")
    if path.startswith("/styles/"):
        return path.rstrip("/").split("/")[-1]
    if path == "/styles":
        return ""
    return ""


def _route(failure: dict[str, Any], matched: tuple[str, str] | None) -> str:
    """``attribute`` revises props; ``location`` revises element loc."""
    if _style_id_of(failure):
        return "attribute"
    message = str(failure.get("message") or "")
    cmd = {
        "command": failure.get("command") or "",
        "path": failure.get("path") or "",
        "type": "style" if _style_id_of(failure) else "",
    }
    kind = classify_tool_error(message, cmd)
    if matched is None:
        return "location"
    if kind == "attribute":
        return "attribute"
    props = [str(item) for item in (failure.get("props") or [])]
    field_props = [item for item in props if item not in {"style", "id", "name", "type", "basedOn"}]
    lowered = message.lower()
    path_missing = any(token in lowered for token in ("not found", "no such", "cannot find", "不存在", "找不到"))
    if field_props and not path_missing:
        return "attribute"
    return "location"


def _assigned_style(loc: dict[str, Any], layer: str, loc_id: str) -> str:
    mapping = (loc.get("by_layer") or {}).get(layer) or {}
    if not isinstance(mapping, dict):
        return ""
    return str(mapping.get(loc_id) or mapping.get(int(loc_id) if loc_id.isdigit() else loc_id) or "")


def plan_repairs(
    failures: list[dict[str, Any]],
    elements: dict[str, Any],
    loc: dict[str, Any],
    catalog: Catalog,
) -> RepairPlan:
    """Split residual apply failures. Frozen locations are not re-identified."""
    rows = _rows(elements)
    real: dict[str, set[str]] = {layer: set() for layer in _LAYERS}
    content: dict[tuple[str, str], str] = {}
    for layer, loc_id, _path, text in rows:
        if layer not in real:
            continue
        real[layer].add(loc_id)
        content[(layer, loc_id)] = text

    catalog_layers = {layer for layer in _LAYERS if catalog.by_layer(layer)}  # type: ignore[arg-type]
    by_layer = loc.get("by_layer") if isinstance(loc.get("by_layer"), dict) else {}

    attribute_ids: set[str] = set()
    attribute_rows: list[dict[str, Any]] = []
    location_failed: dict[str, set[str]] = {layer: set() for layer in _LAYERS}
    attr_lines: list[str] = []
    for failure in failures or []:
        if not isinstance(failure, dict):
            continue
        path = str(failure.get("path") or "")
        matched = _match_path(path, rows)
        route = _route(failure, matched)
        if route == "attribute":
            attribute_rows.append(failure)
            sid = _style_id_of(failure)
            if not sid and matched is not None:
                sid = _assigned_style(loc, matched[0], matched[1])
            if sid:
                attribute_ids.add(sid)
                attr_lines.append(
                    f"- style {sid} path={path} props={failure.get('props') or []} "
                    f"error={failure.get('message') or ''}"
                )
            continue
        if matched is not None and matched[0] in location_failed:
            location_failed[matched[0]].add(matched[1])

    retry: dict[str, list[str]] = {}
    frozen: dict[str, list[str]] = {}
    dropped: dict[str, list[str]] = {}
    for layer in _LAYERS:
        if layer not in catalog_layers:
            continue
        mapping = by_layer.get(layer) if isinstance(by_layer.get(layer), dict) else {}
        assigned = {str(key) for key in mapping}
        candidates = set(real[layer])
        if layer == "paragraph.body":
            candidates = {
                loc_id for loc_id in candidates if str(content.get((layer, loc_id)) or "").strip()
            }
        missing = candidates - assigned
        retry_set = (location_failed[layer] & real[layer]) | missing
        retry[layer] = sorted(retry_set, key=lambda item: (len(item), item))
        frozen[layer] = sorted(real[layer] - retry_set, key=lambda item: (len(item), item))
        dropped[layer] = sorted(assigned - real[layer], key=lambda item: (len(item), item))

    loc_lines: list[str] = []
    for layer, ids in retry.items():
        for loc_id in ids:
            snippet = (content.get((layer, loc_id)) or "").replace("\n", " ")[:180]
            loc_lines.append(f"- {layer} location_id={loc_id} content={snippet}")

    attribute_feedback = ""
    if attr_lines:
        attribute_feedback = (
            "Officecli rejected these attribute names. "
            "Revise only these styles and use whitelist field names:\n" + "\n".join(attr_lines[:40])
        )
    location_feedback = ""
    if loc_lines:
        location_feedback = (
            "These real locations are missing or their path was rejected. "
            "Assign only these location_id values. Do not emit any other location_id:\n"
            + "\n".join(loc_lines[:80])
        )
    return RepairPlan(
        attribute_style_ids=sorted(attribute_ids),
        retry_ids={key: value for key, value in retry.items() if value},
        frozen_ids={key: value for key, value in frozen.items() if value},
        dropped_fake={key: value for key, value in dropped.items() if value},
        attribute_feedback=attribute_feedback,
        location_feedback=location_feedback,
        attribute_failures=attribute_rows,
    )


def merge_props(
    original: dict[str, Any],
    patch: dict[str, Any],
    style_ids: list[str],
) -> dict[str, Any]:
    """Keep every style. Replace only the styles whose fields were rejected."""
    merged = dict(original or {})
    allowed = set(style_ids)
    for sid, spec in (patch or {}).items():
        if str(sid) in allowed and isinstance(spec, dict):
            merged[str(sid)] = spec
    return merged


def merge_assignment(
    original: Assignment,
    patch: Assignment,
    retry_ids: dict[str, list[str]],
    elements: dict[str, Any],
) -> Assignment:
    """Mask frozen locations. Drop assignment keys that are not real inventory ids."""
    rows = _rows(elements)
    real: dict[str, set[str]] = {}
    for layer, loc_id, _path, _content in rows:
        real.setdefault(layer, set()).add(loc_id)
    retry = {layer: set(ids) for layer, ids in (retry_ids or {}).items()}
    merged = Assignment.from_dict(original.to_dict())
    patched = patch.to_dict()
    by_layer = dict(merged.by_layer)
    for layer, mapping in list(by_layer.items()):
        if not isinstance(mapping, dict):
            continue
        kept = {}
        real_ids = real.get(layer, set())
        for key, sid in mapping.items():
            loc_id = str(key)
            if real_ids and loc_id not in real_ids:
                continue
            kept[loc_id] = sid
        incoming = (patched.get("by_layer") or {}).get(layer) or {}
        if isinstance(incoming, dict):
            for key, sid in incoming.items():
                loc_id = str(key)
                if loc_id not in retry.get(layer, set()):
                    continue
                if real_ids and loc_id not in real_ids:
                    continue
                if sid:
                    kept[loc_id] = str(sid)
        by_layer[layer] = kept
    merged.by_layer = by_layer

    def _merge_nested(original_map: dict, patch_map: dict, layer: str) -> dict:
        allowed = retry.get(layer, set())
        real_ids = real.get(layer, set())
        kept = {}
        for key, value in (original_map or {}).items():
            loc_id = str(key)
            if real_ids and loc_id not in real_ids:
                continue
            kept[loc_id] = value
        for key, value in (patch_map or {}).items():
            loc_id = str(key)
            if loc_id not in allowed:
                continue
            if real_ids and loc_id not in real_ids:
                continue
            kept[loc_id] = value
        return kept

    merged.table_cells = _merge_nested(
        merged.table_cells, patched.get("table_cells") or {}, "table"
    )
    merged.paragraph_runs = _merge_nested(
        merged.paragraph_runs, patched.get("paragraph_runs") or {}, "paragraph.body"
    )
    return merged


def filter_elements_for_retry(
    elements: dict[str, Any],
    retry_ids: dict[str, list[str]],
) -> dict[str, list[Any]]:
    """Inventory rows the location repair is allowed to see."""
    retry = {layer: set(ids) for layer, ids in (retry_ids or {}).items()}
    para_ids = retry.get("paragraph.body", set())
    out: dict[str, list[Any]] = {}
    for layer, items in (elements or {}).items():
        kept = []
        for el in items or []:
            if isinstance(el, DocElement):
                loc_id = str(el.location_id)
                meta = el.meta or {}
            elif isinstance(el, dict):
                loc_id = str(el.get("location_id"))
                meta = el.get("meta") or {}
            else:
                continue
            if layer == "run" or str(getattr(el, "layer", layer)) == "run":
                host = str(meta.get("para_location_id") or "")
                if host in para_ids:
                    kept.append(el)
                continue
            if loc_id in retry.get(str(layer), set()) or loc_id in retry.get(
                str(getattr(el, "layer", layer)), set()
            ):
                kept.append(el)
        out[str(layer)] = kept
    return out


def subset_catalog(catalog: Catalog, style_ids: list[str]) -> Catalog:
    allowed = set(style_ids)
    return Catalog(entries=[entry for entry in catalog.entries if entry.style_id in allowed])


def filter_loc_dict(loc: dict[str, Any], retry_ids: dict[str, list[str]]) -> dict[str, Any]:
    """Assignment slice used to compile the second, partial apply."""
    retry = {layer: set(ids) for layer, ids in (retry_ids or {}).items()}
    by_layer = {}
    for layer, mapping in (loc.get("by_layer") or {}).items():
        if not isinstance(mapping, dict):
            continue
        allowed = retry.get(str(layer), set())
        by_layer[str(layer)] = {str(key): value for key, value in mapping.items() if str(key) in allowed}
    return {
        "by_layer": by_layer,
        "table_cells": {
            str(key): value
            for key, value in (loc.get("table_cells") or {}).items()
            if str(key) in retry.get("table", set())
        },
        "paragraph_runs": {
            str(key): value
            for key, value in (loc.get("paragraph_runs") or {}).items()
            if str(key) in retry.get("paragraph.body", set())
        },
    }


def is_style_command(cmd: dict[str, Any]) -> bool:
    path = str(cmd.get("path") or "")
    parent = str(cmd.get("parent") or "")
    return path.startswith("/styles/") or path == "/styles" or parent == "/styles"


def style_id_of_command(cmd: dict[str, Any]) -> str:
    path = str(cmd.get("path") or "")
    if path.startswith("/styles/"):
        return path.rstrip("/").split("/")[-1]
    props = cmd.get("props") if isinstance(cmd.get("props"), dict) else {}
    return str(props.get("id") or "")
