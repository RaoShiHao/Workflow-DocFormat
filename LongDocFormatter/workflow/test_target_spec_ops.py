"""Tests for composable target_spec recipe execution."""

from __future__ import annotations

from LongDocFormatter.workflow.compile_plan import CompilePlan, compile_plan_rule, PlanEnv
from LongDocFormatter.workflow.contracts import Catalog, CatalogEntry, DocElement
from LongDocFormatter.workflow.target_attribute_spec_ops import (
    attribute_spec_mode_from_recipe,
    execute_target_attribute_spec_recipe,
    recipe_from_attribute_spec_mode,
    validate_target_attribute_spec_recipe,
)


def test_recipe_from_attribute_spec_mode_patch():
    recipe = recipe_from_attribute_spec_mode("template_then_patch")
    assert [s["op"] for s in recipe] == ["from_exemplars", "from_text", "overlay"]
    assert recipe[1]["coverage"] == "sparse"


def test_validate_recipe_text_only():
    env_mode = "text_requirement"
    recipe = validate_target_attribute_spec_recipe(
        [{"op": "from_exemplars"}, {"op": "overlay"}],
        input_mode=env_mode,
    )
    assert recipe == [{"op": "from_text", "coverage": "full"}]


def test_compile_plan_rule_includes_recipes():
    env = PlanEnv(has_template=True, has_requirement=True, requirement="change Heading 1 to 14pt")
    plan = compile_plan_rule(env)
    assert plan.target_extract_recipe
    assert plan.target_attribute_spec_recipe
    assert plan.target_element_loc_recipe
    assert plan.document_modify_recipe
    assert attribute_spec_mode_from_recipe(plan.target_attribute_spec_recipe) == plan.attribute_spec_mode


def test_execute_overlay_recipe_without_llm():
    catalog = Catalog(
        entries=[
            CatalogEntry(
                style_id="ParaBody",
                object="paragraph.body",
                display_name="Body",
                description="",
                exemplar_path="p1",
            )
        ]
    )
    template_el = {
        "paragraph.body": [
            DocElement(
                layer="paragraph.body",
                location_id="1",
                path="p1",
                props={"size": "12pt"},
            )
        ]
    }
    state = {"requirement": "", "_template_elements": template_el}

    class LM:
        model = "mock"

        def chat_json(self, *, system, user, **kwargs):
            raise AssertionError("overlay recipe should not call LLM")

    class Loader:
        def load(self, *args, **kwargs):
            return {"system": "", "user_template": ""}

    ws = execute_target_attribute_spec_recipe(
        [{"op": "from_exemplars"}],
        state=state,
        catalog=catalog,
        language_model=LM(),
        prompt_loader=Loader(),
        locale="en",
        llm_kwargs={},
    )
    assert ws.result["ParaBody"]["props"]["size"] == "12pt"
