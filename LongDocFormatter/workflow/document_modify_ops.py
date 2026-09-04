"""Composable document_modify (step 4) ops — produce ``modified_doc``."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from LongDocFormatter.workflow.apply import apply_format
from LongDocFormatter.workflow.contracts import Assignment, Catalog

DOCUMENT_MODIFY_OP_CATALOG: dict[str, str] = {
    "apply_format": "Write target_props + target_loc onto source → output document.",
}

DEFAULT_DOCUMENT_MODIFY_RECIPE: list[dict[str, Any]] = [{"op": "apply_format"}]


def normalize_document_modify_recipe(recipe: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not recipe:
        return [dict(step) for step in DEFAULT_DOCUMENT_MODIFY_RECIPE]
    out = [
        {"op": str(step.get("op") or "apply_format").strip()}
        for step in recipe
        if isinstance(step, dict) and step.get("op")
    ]
    return out or [dict(step) for step in DEFAULT_DOCUMENT_MODIFY_RECIPE]


def execute_document_modify_recipe(
    recipe: list[dict[str, Any]],
    *,
    init_doc: Path,
    output_doc: Path,
    catalog: Catalog,
    target_props: dict[str, Any],
    assignment: Assignment,
    init_elements: dict[str, Any],
) -> Path:
    step = normalize_document_modify_recipe(recipe)[0]
    if step.get("op") != "apply_format":
        step = {"op": "apply_format"}
    apply_format(
        init_doc=init_doc,
        output_doc=output_doc,
        catalog=catalog,
        declarations=target_props,
        assignment=assignment,
        init_elements=init_elements,
    )
    return output_doc
