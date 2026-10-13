"""Copy init.docx → output, then the shared apply engine (officecli batch).

Compile + execute live in ``apply_core`` (same module as the skill copy).
This file only adapts Catalog / Assignment / DocElement to that dict API.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from LongDocFmt.workflow.apply_core import (
    CompiledOps,
    apply_document,
    compile_ops,
    execute_commands,
    execute_inplace,
)
from LongDocFmt.workflow.apply_repair import (
    filter_loc_dict,
    is_style_command,
    style_id_of_command,
)
from LongDocFmt.workflow.contracts import Assignment, Catalog, DocElement, Layer
from LongDocFmt.workflow.officecli_lock import officecli_exclusive


def inventory_from_elements(
    init_elements: Dict[Layer, List[DocElement]] | Dict[str, Any],
    path_index: Dict[tuple[str, str], str] | None = None,
) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for layer, els in (init_elements or {}).items():
        rows: list[dict] = []
        for el in els or []:
            if hasattr(el, "to_dict"):
                d = el.to_dict()
            elif isinstance(el, dict):
                d = dict(el)
            else:
                continue
            key = (str(layer), str(d.get("location_id")))
            if path_index and key in path_index:
                d["path"] = path_index[key]
            rows.append(d)
        out[str(layer)] = rows
    if path_index:
        for (layer, loc_id), path in path_index.items():
            rows = out.setdefault(str(layer), [])
            if any(str(r.get("location_id")) == str(loc_id) for r in rows):
                continue
            rows.append({"layer": layer, "location_id": loc_id, "path": path, "props": {}, "content": "", "meta": {}})
    return out


def collect_apply_commands(
    *,
    catalog: Catalog,
    declarations: Dict[str, Dict[str, Any]],
    assignment: Assignment,
    path_index: Dict[tuple[str, str], str],
    init_elements: Dict[Layer, List[DocElement]],
) -> list[dict[str, Any]]:
    """Compile batchable ops (kept for tests / callers)."""
    compiled = compile_apply(
        catalog=catalog,
        declarations=declarations,
        assignment=assignment,
        path_index=path_index,
        init_elements=init_elements,
    )
    return compiled.commands


def compile_apply(
    *,
    catalog: Catalog,
    declarations: Dict[str, Dict[str, Any]],
    assignment: Assignment,
    path_index: Dict[tuple[str, str], str] | None = None,
    init_elements: Dict[Layer, List[DocElement]] | None = None,
) -> CompiledOps:
    idx = dict(path_index or {})
    if not idx and init_elements:
        for layer, els in init_elements.items():
            for el in els or []:
                idx[(str(layer), str(el.location_id))] = el.path
    inventory = inventory_from_elements(init_elements or {}, idx)
    return compile_ops(
        catalog_entries=[e.to_dict() for e in catalog.entries],
        props=declarations,
        loc=assignment.to_dict(),
        inventory=inventory,
    )


def apply_format(
    *,
    init_doc: Path,
    output_doc: Path,
    catalog: Catalog,
    declarations: Dict[str, Dict[str, Any]],
    assignment: Assignment,
    init_elements: Dict[Layer, List[DocElement]],
) -> dict[str, Any]:
    path_index: Dict[tuple[str, str], str] = {}
    for layer, els in init_elements.items():
        for el in els:
            path_index[(str(layer), str(el.location_id))] = el.path
    inventory = inventory_from_elements(init_elements, path_index)
    with officecli_exclusive():
        return apply_document(
            source=init_doc,
            output=output_doc,
            catalog_entries=[e.to_dict() for e in catalog.entries],
            props=declarations,
            loc=assignment.to_dict(),
            inventory=inventory,
        )


def apply_repaired(
    *,
    output_doc: Path,
    catalog: Catalog,
    declarations: Dict[str, Dict[str, Any]],
    assignment: Assignment,
    init_elements: Dict[Layer, List[DocElement]],
    style_ids: list[str],
    retry_ids: dict[str, list[str]],
) -> dict[str, Any]:
    """Write only repaired styles and locations onto the existing output."""
    path_index: Dict[tuple[str, str], str] = {}
    for layer, els in init_elements.items():
        for el in els:
            path_index[(str(layer), str(el.location_id))] = el.path
    inventory = inventory_from_elements(init_elements, path_index)
    entries = [e.to_dict() for e in catalog.entries]
    props = declarations
    commands: list[dict[str, Any]] = []
    find_cmds: list[dict[str, Any]] = []
    wanted = {str(sid) for sid in style_ids}
    if wanted:
        compiled = compile_ops(
            catalog_entries=entries,
            props=props,
            loc=assignment.to_dict(),
            inventory=inventory,
        )
        bound_paths: set[str] = set()
        for layer, mapping in (assignment.by_layer or {}).items():
            if not isinstance(mapping, dict):
                continue
            for loc_id, sid in mapping.items():
                if str(sid) not in wanted:
                    continue
                bound = path_index.get((str(layer), str(loc_id)))
                if bound:
                    bound_paths.add(str(bound))
        for cmd in compiled.commands:
            if is_style_command(cmd) and style_id_of_command(cmd) in wanted:
                commands.append(cmd)
                continue
            path = str(cmd.get("path") or "")
            if any(path == item or path.startswith(item.rstrip("/") + "/") for item in bound_paths):
                commands.append(cmd)
    if any(retry_ids.values()):
        compiled = compile_ops(
            catalog_entries=entries,
            props=props,
            loc=filter_loc_dict(assignment.to_dict(), retry_ids),
            inventory=inventory,
        )
        commands.extend(cmd for cmd in compiled.commands if not is_style_command(cmd))
        find_cmds.extend(compiled.find_cmds)
    with officecli_exclusive():
        return execute_inplace(output_doc, commands, find_cmds)


# Re-export for callers that imported execute from apply.
__all__ = [
    "apply_format",
    "collect_apply_commands",
    "compile_apply",
    "execute_commands",
    "inventory_from_elements",
]
