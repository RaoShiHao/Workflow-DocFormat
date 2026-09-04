"""Pipeline step executors driven by :class:`CompilePlan` recipes."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from LongDocFormatter.workflow.bilingual_prompt_loader import PromptLoader
from LongDocFormatter.workflow.compile_plan import CompilePlan, plan_from_state
from LongDocFormatter.workflow.contracts import Catalog, Layer
from LongDocFormatter.workflow.target_attribute_spec_ops import (
    TargetPropsWorkspace,
    execute_target_attribute_spec_recipe,
)
from LongDocFormatter.workflow.target_extract_ops import execute_target_extract_recipe


@dataclass
class PipelineContext:
    language_model: Any
    prompt_loader: PromptLoader
    enabled_layers: List[Layer]
    skip_single_catalog: bool
    locale: str
    llm_kwargs: Dict[str, Any]
    catalog_max_examples: int = 3
    catalog_max_content_chars: int = 800
    compile_plan: CompilePlan | None = None
    target_attribute_spec_warnings: List[Dict[str, Any]] | None = None
    target_props_workspace: TargetPropsWorkspace | None = None

    # Legacy aliases
    @property
    def target_spec_warnings(self) -> List[Dict[str, Any]] | None:
        return self.target_attribute_spec_warnings

    @property
    def target_spec_workspace(self) -> TargetPropsWorkspace | None:
        return self.target_props_workspace


def build_target_set(state: Dict[str, Any], *, ctx: PipelineContext) -> Catalog:
    plan = ctx.compile_plan or plan_from_state(state)
    return execute_target_extract_recipe(
        plan.target_extract_recipe,
        state=state,
        language_model=ctx.language_model,
        prompt_loader=ctx.prompt_loader,
        enabled_layers=ctx.enabled_layers,
        locale=ctx.locale,
        llm_kwargs=ctx.llm_kwargs,
        skip_single_catalog=ctx.skip_single_catalog,
        catalog_max_examples=ctx.catalog_max_examples,
        catalog_max_content_chars=ctx.catalog_max_content_chars,
        intro_enrich=plan.intro_enrich,
    )


def build_target_props(
    state: Dict[str, Any],
    catalog: Catalog,
    *,
    ctx: PipelineContext,
) -> Dict[str, Dict[str, Any]]:
    plan = ctx.compile_plan or plan_from_state(state)
    ws = execute_target_attribute_spec_recipe(
        plan.target_attribute_spec_recipe,
        state=state,
        catalog=catalog,
        language_model=ctx.language_model,
        prompt_loader=ctx.prompt_loader,
        locale=ctx.locale,
        llm_kwargs=ctx.llm_kwargs,
        warnings=ctx.target_attribute_spec_warnings,
    )
    ctx.target_props_workspace = ws
    return ws.result


# Legacy aliases
StrategyContext = PipelineContext
build_target_attributes = build_target_props


def strategy_for_state(state: Dict[str, Any]) -> str:
    return plan_from_state(state).strategy
