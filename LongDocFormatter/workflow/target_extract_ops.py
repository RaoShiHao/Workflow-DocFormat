"""Composable target_extract (step 1) ops — build ``target_set``."""
from __future__ import annotations

from typing import Any, Dict, List

from LongDocFormatter.workflow.catalog import catalog_from_text, cluster_elements, induce_catalog_from_all_layers
from LongDocFormatter.workflow.contracts import Catalog, Layer

TARGET_EXTRACT_OP_CATALOG: dict[str, str] = {
    "from_text": "LLM induce formatting roles from requirement text.",
    "from_template": "Cluster template.docx elements into roles (optional text for naming).",
}

DEFAULT_EXTRACT_RECIPES: dict[str, list[dict[str, Any]]] = {
    "text_requirement": [{"op": "from_text"}],
    "template": [{"op": "from_template"}],
    "template_w_text": [{"op": "from_template", "use_text_for_naming": True}],
}


def recipe_from_extract_source(source: str) -> list[dict[str, Any]]:
    if source == "from_text":
        return [dict(step) for step in DEFAULT_EXTRACT_RECIPES["text_requirement"]]
    if source == "from_template_with_text":
        return [dict(step) for step in DEFAULT_EXTRACT_RECIPES["template_w_text"]]
    return [dict(step) for step in DEFAULT_EXTRACT_RECIPES["template"]]


def extract_source_from_recipe(recipe: list[dict[str, Any]]) -> str:
    step = _normalize_step(recipe[0]) if recipe else {"op": "from_text"}
    if step.get("op") == "from_text":
        return "from_text"
    if step.get("use_text_for_naming"):
        return "from_template_with_text"
    return "from_template"


def normalize_target_extract_recipe(
    recipe: list[dict[str, Any]] | None,
    *,
    fallback_source: str,
) -> list[dict[str, Any]]:
    if not recipe:
        return recipe_from_extract_source(fallback_source)
    out = [_normalize_step(step) for step in recipe if isinstance(step, dict) and step.get("op")]
    return out or recipe_from_extract_source(fallback_source)


def _normalize_step(step: dict[str, Any]) -> dict[str, Any]:
    op = str(step.get("op") or "").strip()
    normalized: dict[str, Any] = {"op": op}
    if step.get("use_text_for_naming") in (True, "true", "True", 1):
        normalized["use_text_for_naming"] = True
    return normalized


def validate_target_extract_recipe(recipe: list[dict[str, Any]], *, input_mode: str) -> list[dict[str, Any]]:
    if input_mode == "text_requirement":
        return [{"op": "from_text"}]
    if input_mode == "template":
        return [{"op": "from_template"}]
    step = _normalize_step(recipe[0]) if recipe else {"op": "from_template", "use_text_for_naming": True}
    if step.get("op") != "from_template":
        return [{"op": "from_template", "use_text_for_naming": True}]
    return [step]


def _joint_naming_prompt(prompt_loader: Any, locale: str) -> Dict[str, str]:
    prompt = prompt_loader.load("style_naming_joint", locale=locale, section="style_naming_joint")
    if prompt.get("user_template"):
        return prompt
    return prompt_loader.load("paragraph_body", locale=locale, section="style_naming_batch")


def execute_target_extract_recipe(
    recipe: list[dict[str, Any]],
    *,
    state: dict[str, Any],
    language_model: Any,
    prompt_loader: Any,
    enabled_layers: List[Layer],
    locale: str,
    llm_kwargs: dict[str, Any],
    skip_single_catalog: bool,
    catalog_max_examples: int,
    catalog_max_content_chars: int,
    intro_enrich: str = "skip",
) -> Catalog:
    requirement = str(state.get("requirement") or state.get("text_input") or "")
    step = _normalize_step(recipe[0]) if recipe else {"op": "from_text"}
    op = step.get("op")

    if op == "from_text":
        prompt = prompt_loader.load("catalog_from_text", locale=locale, section="catalog_from_text")
        return catalog_from_text(
            text=requirement,
            language_model=language_model,
            prompt=prompt,
            llm_kwargs=llm_kwargs,
        )

    template_el = state.get("_template_elements") or {}
    use_text = bool(step.get("use_text_for_naming")) or intro_enrich == "llm_fill"
    if use_text and not requirement.strip():
        use_text = False
    clusters_by_layer: dict[Layer, list] = {}
    for layer in enabled_layers:
        elements = template_el.get(layer) or []
        if not elements:
            continue
        clusters_by_layer[layer] = cluster_elements(elements)
    _ = skip_single_catalog  # joint naming always runs so Tbl* can cite Sec*
    catalog, _decls = induce_catalog_from_all_layers(
        clusters_by_layer=clusters_by_layer,
        language_model=language_model,
        prompt=_joint_naming_prompt(prompt_loader, locale),
        llm_kwargs=llm_kwargs,
        requirement=requirement if use_text else "",
        max_cluster_examples=int(catalog_max_examples or 3),
        max_content_chars=int(catalog_max_content_chars or 800),
        include_props=True,
    )
    return catalog
