"""Composable target_attribute_spec (step 2) ops — build ``target_props``."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Literal

from LongDocFormatter.workflow.attribute_overlay import overlay_target_attributes
from LongDocFormatter.workflow.contracts import Catalog
from LongDocFormatter.workflow.declarations import (
    declarations_from_exemplars,
    declarations_from_text,
    extract_requirement_delta,
)

TextCoverage = Literal["full", "sparse"]

TARGET_ATTRIBUTE_SPEC_OP_CATALOG: dict[str, str] = {
    "from_exemplars": "Read whitelist props from template exemplars (no LLM).",
    "from_text": "LLM fill from requirement + target_set; coverage=full|sparse.",
    "overlay": "Deterministic merge: base + patch → target_props (no LLM).",
}

DEFAULT_ATTRIBUTE_SPEC_RECIPES: dict[str, list[dict[str, Any]]] = {
    "from_text": [{"op": "from_text", "coverage": "full"}],
    "from_template": [{"op": "from_exemplars"}],
    "template_then_patch": [
        {"op": "from_exemplars"},
        {"op": "from_text", "coverage": "sparse"},
        {"op": "overlay"},
    ],
}


@dataclass
class TargetPropsWorkspace:
    base: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    patch: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    result: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    step_outputs: Dict[str, Dict[str, Dict[str, Any]]] = field(default_factory=dict)


# Backward-compatible aliases
TargetSpecWorkspace = TargetPropsWorkspace


def recipe_from_attribute_spec_mode(mode: str) -> list[dict[str, Any]]:
    return [
        dict(step)
        for step in DEFAULT_ATTRIBUTE_SPEC_RECIPES.get(str(mode), DEFAULT_ATTRIBUTE_SPEC_RECIPES["from_text"])
    ]


def attribute_spec_mode_from_recipe(recipe: list[dict[str, Any]]) -> str:
    ops = [_normalize_step(step) for step in recipe]
    names = [step["op"] for step in ops]
    if names == ["from_exemplars"]:
        return "from_template"
    if names == ["from_text"] and ops[0].get("coverage", "full") == "full":
        return "from_text"
    if "overlay" in names and "from_exemplars" in names:
        return "template_then_patch"
    if names == ["from_text"]:
        return "from_text"
    return "from_text"


def normalize_target_attribute_spec_recipe(
    recipe: list[dict[str, Any]] | None,
    *,
    fallback_mode: str,
) -> list[dict[str, Any]]:
    if not recipe:
        return recipe_from_attribute_spec_mode(fallback_mode)
    out = [_normalize_step(step) for step in recipe if isinstance(step, dict) and step.get("op")]
    return out or recipe_from_attribute_spec_mode(fallback_mode)


# Legacy alias
normalize_target_spec_recipe = normalize_target_attribute_spec_recipe


def _normalize_step(step: dict[str, Any]) -> dict[str, Any]:
    op = str(step.get("op") or "").strip()
    normalized: dict[str, Any] = {"op": op}
    coverage = str(step.get("coverage") or "full").strip().lower()
    if coverage in ("full", "sparse"):
        normalized["coverage"] = coverage
    return normalized


def validate_target_attribute_spec_recipe(
    recipe: list[dict[str, Any]],
    *,
    input_mode: str,
) -> list[dict[str, Any]]:
    ops = [_normalize_step(step) for step in recipe]
    names = [step["op"] for step in ops]

    if input_mode == "text_requirement":
        return [{"op": "from_text", "coverage": "full"}]
    if input_mode == "template":
        return [{"op": "from_exemplars"}]

    if "from_text" in names and ops[names.index("from_text")].get("coverage") == "full":
        return [{"op": "from_exemplars"}]
    if "overlay" in names:
        return recipe_from_attribute_spec_mode("template_then_patch")
    if names == ["from_exemplars"] or not names:
        return [{"op": "from_exemplars"}]
    return ops


# Legacy alias
validate_target_spec_recipe = validate_target_attribute_spec_recipe


def execute_target_attribute_spec_recipe(
    recipe: list[dict[str, Any]],
    *,
    state: dict[str, Any],
    catalog: Catalog,
    language_model: Any,
    prompt_loader: Any,
    locale: str,
    llm_kwargs: dict[str, Any],
    warnings: list[dict[str, Any]] | None = None,
) -> TargetPropsWorkspace:
    ws = TargetPropsWorkspace()
    requirement = str(state.get("requirement") or state.get("text_input") or "")
    template_elements = state.get("_template_elements") or {}

    for raw_step in recipe:
        step = _normalize_step(raw_step)
        op = step["op"]
        if op == "from_exemplars":
            out = declarations_from_exemplars(catalog, layer_elements=template_elements)
            ws.base = out
            ws.result = out
            ws.step_outputs["from_exemplars"] = out
            continue

        if op == "from_text":
            coverage: TextCoverage = step.get("coverage", "full")  # type: ignore[assignment]
            if coverage == "sparse":
                text = extract_requirement_delta(requirement)
                if not text.strip():
                    ws.patch = {}
                    ws.step_outputs["from_text"] = {}
                    continue
                prompt = prompt_loader.load(
                    "target_attributes_patch",
                    locale=locale,
                    section="target_attributes_patch",
                )
                out = declarations_from_text(
                    catalog=catalog,
                    text=text,
                    language_model=language_model,
                    prompt=prompt,
                    llm_kwargs=llm_kwargs,
                    locale=locale,
                    warnings=warnings,
                    coverage="sparse",
                    base_for_context=ws.base,
                )
            else:
                prompt = prompt_loader.load(
                    "declarations_text", locale=locale, section="declarations_text"
                )
                out = declarations_from_text(
                    catalog=catalog,
                    text=requirement,
                    language_model=language_model,
                    prompt=prompt,
                    llm_kwargs=llm_kwargs,
                    locale=locale,
                    warnings=warnings,
                    coverage="full",
                )
            if coverage == "sparse":
                ws.patch = out
            else:
                ws.result = out
            ws.step_outputs["from_text"] = out
            continue

        if op == "overlay":
            merged = overlay_target_attributes(ws.base, ws.patch)
            ws.result = merged
            ws.step_outputs["overlay"] = merged
            continue

    return ws


# Legacy alias
execute_target_spec_recipe = execute_target_attribute_spec_recipe
