"""Apply IR, then one feedback pass.

Attribute failures are rewritten as officecli commands. There is no second
attribute-spec call. Location failures still re-identify only the failed ids.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from LongDocFmt.workflow.apply import apply_format, apply_repaired, collect_apply_commands
from LongDocFmt.workflow.apply_core import execute_inplace
from LongDocFmt.workflow.apply_repair import (
    filter_elements_for_retry,
    merge_assignment,
    plan_repairs,
)
from LongDocFmt.workflow.command_feedback import revise_failed_commands
from LongDocFmt.workflow.contracts import Assignment, Catalog
from LongDocFmt.workflow.json_util import LlmJsonParseError
from LongDocFmt.workflow.llm_trace import LlmBudgetExceeded
from LongDocFmt.workflow.officecli_lock import officecli_exclusive


def apply_ir_command_feedback(
    *,
    init_doc: Path,
    output_doc: Path,
    catalog: Catalog,
    declarations: dict[str, Any],
    assignment: Assignment,
    init_elements: dict[str, Any],
    language_model: Any,
    llm_kwargs: dict[str, Any] | None = None,
    env_feedback: bool = False,
    localize: Callable[..., Assignment] | None = None,
) -> tuple[Assignment, dict[str, Any]]:
    """Full apply from source, then at most one attribute-command fix and one loc fix."""
    stats = apply_format(
        init_doc=init_doc,
        output_doc=output_doc,
        catalog=catalog,
        declarations=declarations,
        assignment=assignment,
        init_elements=init_elements,
    )
    if not isinstance(stats, dict):
        stats = {}
    if not env_feedback:
        return assignment, stats

    repair = plan_repairs(
        list(stats.get("failures") or []),
        init_elements,
        assignment.to_dict(),
        catalog,
    )
    routed: list[str] = []
    current = assignment
    if repair.needs_attribute_repair:
        compiled = collect_apply_commands(
            catalog=catalog,
            declarations=declarations,
            assignment=current,
            path_index={},
            init_elements=init_elements,
        )
        revised = revise_failed_commands(
            language_model,
            compiled,
            repair.attribute_failures,
            llm_kwargs=llm_kwargs,
        )
        if revised:
            with officecli_exclusive():
                stats = execute_inplace(output_doc, revised)
        routed.append("attribute_commands")
    if repair.needs_location_repair or any(repair.dropped_fake.values()):
        revised_loc = Assignment()
        if repair.needs_location_repair and localize is not None:
            try:
                revised_loc = localize(
                    filter_elements_for_retry(init_elements, repair.retry_ids),
                    declarations,
                    repair.location_feedback,
                )
            except (LlmBudgetExceeded, LlmJsonParseError):
                revised_loc = Assignment()
        current = merge_assignment(
            current,
            revised_loc,
            repair.retry_ids if repair.needs_location_repair else {},
            init_elements,
        )
        if repair.needs_location_repair:
            stats = apply_repaired(
                output_doc=output_doc,
                catalog=catalog,
                declarations=declarations,
                assignment=current,
                init_elements=init_elements,
                style_ids=[],
                retry_ids=repair.retry_ids,
            )
            routed.append("location")
        elif repair.dropped_fake:
            routed.append("drop_fake")
    stats = dict(stats)
    stats["env_feedback_routed"] = routed
    stats["ok"] = int(stats.get("n_failed") or 0) == 0
    return current, stats
