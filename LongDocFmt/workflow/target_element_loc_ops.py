"""Composable target_element_loc (step 3) ops — build ``target_loc``."""
from __future__ import annotations

from typing import Any, Dict

from LongDocFmt.workflow.assignment import build_assignment, build_assignment_flat
from LongDocFmt.workflow.contracts import Assignment, Catalog

TARGET_ELEMENT_LOC_OP_CATALOG: dict[str, str] = {
    "assign_by_layer": "LLM map init elements to target_set style_ids (by layer).",
    "assign_flat": "LLM map all layers in one joint call (NoLayeredLoc ablation).",
}

DEFAULT_TARGET_LOC_RECIPE: list[dict[str, Any]] = [{"op": "assign_by_layer"}]


def normalize_target_element_loc_recipe(recipe: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not recipe:
        return [dict(step) for step in DEFAULT_TARGET_LOC_RECIPE]
    out = []
    for step in recipe:
        if not isinstance(step, dict) or not step.get("op"):
            continue
        op = str(step.get("op") or "assign_by_layer").strip()
        if op not in TARGET_ELEMENT_LOC_OP_CATALOG:
            op = "assign_by_layer"
        out.append({"op": op})
    return out or [dict(step) for step in DEFAULT_TARGET_LOC_RECIPE]


def execute_target_element_loc_recipe(
    recipe: list[dict[str, Any]],
    *,
    catalog: Catalog,
    init_elements: dict[str, Any],
    target_props: dict[str, Any],
    language_model: Any,
    prompts: Dict[str, Dict[str, str]],
    llm_kwargs: dict[str, Any],
    batch_sizes: dict[str, Any],
    skip_if_single: bool,
    multimodal_model: Any | None,
    mm_kwargs: dict[str, Any],
    llm_workers: int,
    init_cues: dict[str, Any],
    table_context_paragraphs: int,
    image_context_paragraphs: int,
    env_feedback: bool = False,
    feedback_note: str = "",
) -> Assignment:
    step = normalize_target_element_loc_recipe(recipe)[0]
    op = str(step.get("op") or "assign_by_layer")
    common = dict(
        catalog=catalog,
        init_elements=init_elements,
        language_model=language_model,
        prompts=prompts,
        llm_kwargs=llm_kwargs,
        skip_if_single=skip_if_single,
        declarations=target_props,
        init_cues=init_cues,
        table_context_paragraphs=table_context_paragraphs,
        image_context_paragraphs=image_context_paragraphs,
        env_feedback=env_feedback,
        feedback_note=feedback_note,
    )
    if op == "assign_flat":
        return build_assignment_flat(**common)
    return build_assignment(
        **common,
        batch_sizes=batch_sizes,
        multimodal_model=multimodal_model,
        mm_kwargs=mm_kwargs,
        llm_workers=llm_workers,
    )
