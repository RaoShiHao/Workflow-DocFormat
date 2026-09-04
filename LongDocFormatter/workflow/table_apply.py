"""Table chrome compile — delegates to the shared apply engine.

Kept so existing imports of ``collect_table_commands`` / ``apply_tables`` still
work. Cell paths prefer inventory (P3); alias lists are retry-only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from LongDocFormatter.workflow.apply_core import compile_ops, execute_commands
from LongDocFormatter.workflow.apply import inventory_from_elements
from LongDocFormatter.workflow.contracts import Assignment, DocElement, Layer


def collect_table_commands(
    declarations: Dict[str, Dict[str, Any]],
    assignment: Assignment,
    init_elements: Dict[Layer, List[DocElement]],
) -> list[dict[str, Any]]:
    loc = assignment.to_dict()
    loc["by_layer"] = {"table": dict((assignment.by_layer or {}).get("table") or {})}
    compiled = compile_ops(
        catalog_entries=[],
        props=declarations,
        loc=loc,
        inventory=inventory_from_elements(init_elements),
    )
    return [c for c in compiled.commands if c.get("command") in {"set", "add"}]


def apply_tables(
    doc: Path,
    declarations: Dict[str, Dict[str, Any]],
    assignment: Assignment,
    init_elements: Dict[Layer, List[DocElement]],
) -> None:
    execute_commands(doc, collect_table_commands(declarations, assignment, init_elements))
