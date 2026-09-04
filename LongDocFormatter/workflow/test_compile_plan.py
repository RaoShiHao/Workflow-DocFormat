"""Tests for constrained compile-plan validation."""

from __future__ import annotations

from LongDocFormatter.workflow.compile_plan import (
    CompilePlan,
    PlanEnv,
    build_compile_plan,
    compile_plan_rule,
    derive_input_mode,
    validate_compile_plan,
)


def test_derive_input_mode_matrix():
    assert derive_input_mode(PlanEnv(False, False)) == "text_requirement"
    assert derive_input_mode(PlanEnv(True, False)) == "template"
    assert derive_input_mode(PlanEnv(True, True)) == "template_w_text"
    assert derive_input_mode(PlanEnv(False, True)) == "text_requirement"


def test_template_only_clamps_llm_choices():
    env = PlanEnv(has_template=True, has_requirement=False)
    draft = CompilePlan(
        input_mode="template_w_text",
        target_extract_source="from_template_with_text",
        intro_enrich="llm_fill",
        attribute_spec_mode="template_then_patch",
        strategy="template_backed",
        planner="llm",
    )
    plan = validate_compile_plan(draft, env)
    assert plan.input_mode == "template"
    assert plan.attribute_spec_mode == "from_template"
    assert plan.intro_enrich == "skip"


def test_template_w_text_delta_section_triggers_patch_rule():
    text = "## Adjustments relative to the template\n- For **Section**, change font size to 14."
    env = PlanEnv(has_template=True, has_requirement=True, requirement=text)
    plan = compile_plan_rule(env)
    assert plan.attribute_spec_mode == "template_then_patch"


def test_build_compile_plan_rule_fallback_without_llm():
    env = PlanEnv(has_template=False, has_requirement=True, requirement="Use 12pt body text.")
    plan = build_compile_plan(env, language_model=None, prompt=None, use_llm=True)
    assert plan.planner == "rule"
    assert plan.attribute_spec_mode == "from_text"
