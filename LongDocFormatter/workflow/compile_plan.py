"""Constrained plan-and-execute: compose recipes for the four IR stages.

Planner inputs: has_template, has_requirement, requirement text, locale.
Code executes recipes only — no ad-hoc routing in graph nodes.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from LongDocFormatter.workflow.contracts import InputMode
from LongDocFormatter.workflow.json_util import parse_llm_json
from LongDocFormatter.workflow.target_attribute_spec_ops import (
    attribute_spec_mode_from_recipe,
    normalize_target_attribute_spec_recipe,
    recipe_from_attribute_spec_mode,
    validate_target_attribute_spec_recipe,
)
from LongDocFormatter.workflow.target_element_loc_ops import DEFAULT_TARGET_LOC_RECIPE, normalize_target_element_loc_recipe
from LongDocFormatter.workflow.document_modify_ops import DEFAULT_DOCUMENT_MODIFY_RECIPE, normalize_document_modify_recipe
from LongDocFormatter.workflow.target_extract_ops import (
    extract_source_from_recipe,
    normalize_target_extract_recipe,
    recipe_from_extract_source,
    validate_target_extract_recipe,
)

TargetExtractSource = Literal["from_text", "from_template", "from_template_with_text"]
IntroEnrichMode = Literal["skip", "llm_fill"]
AttributeSpecMode = Literal["from_text", "from_template", "template_then_patch"]

_PATCH_HINT_RE = re.compile(
    r"(改|调整|修改|增大|减小|改为|换成|set\s+to|change\s+to|instead\s+of|"
    r"\d+\s*pt|outline|margin|bold|italic|line\s*spacing|font)",
    re.I,
)
_NAMING_ONLY_RE = re.compile(
    r"(命名|名称|叫什么|display\s*name|role\s*name|称呼)",
    re.I,
)
_DELTA_SECTION_RE = re.compile(
    r"(adjustments\s+relative\s+to\s+the\s+template|相对模板的格式调整)",
    re.I,
)

_EMPTY_REQUIREMENT_PLACEHOLDER_ZH = "（未提供单独的需求说明；请仅依据 template.docx 进行格式调整。）"
_EMPTY_REQUIREMENT_PLACEHOLDER_EN = (
    "(No separate requirement text; format the document using template.docx only.)"
)


@dataclass
class PlanEnv:
    has_template: bool
    has_requirement: bool
    requirement: str = ""
    locale: str = "en"

    @classmethod
    def from_state(cls, state: dict[str, Any]) -> "PlanEnv":
        template = str(state.get("template") or state.get("template_doc_path") or "").strip()
        requirement = str(state.get("requirement") or state.get("text_input") or "").strip()
        if "has_template" in state:
            has_template = bool(state.get("has_template"))
        else:
            has_template = bool(template)
        if "has_requirement" in state:
            has_requirement = bool(state.get("has_requirement"))
        else:
            has_requirement = bool(requirement)
        return cls(
            has_template=has_template,
            has_requirement=has_requirement,
            requirement=requirement,
            locale=str(state.get("locale") or "en"),
        )


@dataclass
class CompilePlan:
    """Executable recipes for one sample (``compile_plan.json``)."""

    input_mode: InputMode
    target_extract_source: TargetExtractSource
    intro_enrich: IntroEnrichMode
    attribute_spec_mode: AttributeSpecMode
    strategy: str
    target_extract_recipe: list[dict[str, Any]] = field(default_factory=list)
    target_attribute_spec_recipe: list[dict[str, Any]] = field(default_factory=list)
    target_element_loc_recipe: list[dict[str, Any]] = field(default_factory=lambda: [dict(s) for s in DEFAULT_TARGET_LOC_RECIPE])
    document_modify_recipe: list[dict[str, Any]] = field(default_factory=lambda: [dict(s) for s in DEFAULT_DOCUMENT_MODIFY_RECIPE])
    notes: str = ""
    signals: dict[str, Any] = field(default_factory=dict)
    planner: str = "rule"

    @property
    def target_spec_recipe(self) -> list[dict[str, Any]]:
        """Legacy alias for step-2 recipe."""
        return self.target_attribute_spec_recipe

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if not data.get("target_extract_recipe"):
            data["target_extract_recipe"] = recipe_from_extract_source(self.target_extract_source)
        if not data.get("target_attribute_spec_recipe"):
            data["target_attribute_spec_recipe"] = recipe_from_attribute_spec_mode(self.attribute_spec_mode)
        data["target_spec_recipe"] = data["target_attribute_spec_recipe"]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "CompilePlan":
        d = data or {}
        extract_src = str(d.get("target_extract_source") or "from_text")
        attr = str(d.get("attribute_spec_mode") or "from_text")

        extract_raw = d.get("target_extract_recipe")
        if isinstance(extract_raw, list) and extract_raw:
            extract_recipe = normalize_target_extract_recipe(extract_raw, fallback_source=extract_src)
            extract_src = extract_source_from_recipe(extract_recipe)
        else:
            extract_recipe = recipe_from_extract_source(extract_src)

        attr_raw = d.get("target_attribute_spec_recipe") or d.get("target_spec_recipe")
        if isinstance(attr_raw, list) and attr_raw:
            attr_recipe = normalize_target_attribute_spec_recipe(attr_raw, fallback_mode=attr)
            attr = attribute_spec_mode_from_recipe(attr_recipe)
        else:
            attr_recipe = recipe_from_attribute_spec_mode(attr)

        loc_recipe = normalize_target_element_loc_recipe(d.get("target_element_loc_recipe"))
        modify_recipe = normalize_document_modify_recipe(d.get("document_modify_recipe"))

        return cls(
            input_mode=str(d.get("input_mode") or "text_requirement"),  # type: ignore[arg-type]
            target_extract_source=extract_src,  # type: ignore[arg-type]
            intro_enrich=str(d.get("intro_enrich") or "skip"),  # type: ignore[arg-type]
            attribute_spec_mode=attr,  # type: ignore[arg-type]
            strategy=str(d.get("strategy") or "text_requirement"),
            target_extract_recipe=extract_recipe,
            target_attribute_spec_recipe=attr_recipe,
            target_element_loc_recipe=loc_recipe,
            document_modify_recipe=modify_recipe,
            notes=str(d.get("notes") or ""),
            signals=dict(d.get("signals") or {}),
            planner=str(d.get("planner") or "rule"),
        )


def derive_input_mode(env: PlanEnv) -> InputMode:
    if env.has_template and env.has_requirement:
        return "template_w_text"
    if env.has_template:
        return "template"
    return "text_requirement"


def _fixed_plan_fields(input_mode: InputMode) -> dict[str, str]:
    if input_mode == "text_requirement":
        return {"target_extract_source": "from_text", "strategy": "text_requirement"}
    if input_mode == "template":
        return {"target_extract_source": "from_template", "strategy": "template_backed"}
    return {"target_extract_source": "from_template_with_text", "strategy": "template_backed"}


def _text_suggests_patch(text: str) -> bool:
    t = str(text or "").strip()
    if not t:
        return False
    if _DELTA_SECTION_RE.search(t):
        return True
    if _PATCH_HINT_RE.search(t) and not _NAMING_ONLY_RE.search(t[:200]):
        return True
    return bool(_PATCH_HINT_RE.search(t))


def _plan_signals(env: PlanEnv, *, patch_hint: bool = False) -> dict[str, Any]:
    return {
        "has_template": env.has_template,
        "has_requirement": env.has_requirement,
        "has_text": bool(env.requirement.strip()),
        "patch_hint": patch_hint,
    }


def compile_plan_rule(env: PlanEnv) -> CompilePlan:
    mode = derive_input_mode(env)
    fixed = _fixed_plan_fields(mode)
    text = env.requirement.strip()
    patch_hint = _text_suggests_patch(text)

    if mode == "text_requirement":
        return CompilePlan(
            input_mode=mode,
            target_extract_source=fixed["target_extract_source"],  # type: ignore[arg-type]
            intro_enrich="llm_fill" if text else "skip",
            attribute_spec_mode="from_text",
            strategy=fixed["strategy"],
            target_extract_recipe=recipe_from_extract_source("from_text"),
            target_attribute_spec_recipe=recipe_from_attribute_spec_mode("from_text"),
            notes="Text-only: target_extract(from_text) + target_attribute_spec(from_text:full).",
            signals=_plan_signals(env),
            planner="rule",
        )

    if mode == "template":
        return CompilePlan(
            input_mode=mode,
            target_extract_source=fixed["target_extract_source"],  # type: ignore[arg-type]
            intro_enrich="skip",
            attribute_spec_mode="from_template",
            strategy=fixed["strategy"],
            target_extract_recipe=recipe_from_extract_source("from_template"),
            target_attribute_spec_recipe=recipe_from_attribute_spec_mode("from_template"),
            notes="Template-only: target_extract(from_template) + target_attribute_spec(from_exemplars).",
            signals=_plan_signals(env),
            planner="rule",
        )

    attr_mode: AttributeSpecMode = "template_then_patch" if patch_hint else "from_template"
    note = (
        "Template + text: exemplars + from_text(sparse) + overlay."
        if patch_hint
        else "Template + text (naming): from_template + from_exemplars."
    )
    return CompilePlan(
        input_mode=mode,
        target_extract_source=fixed["target_extract_source"],  # type: ignore[arg-type]
        intro_enrich="llm_fill" if text else "skip",
        attribute_spec_mode=attr_mode,
        strategy=fixed["strategy"],
        target_extract_recipe=recipe_from_extract_source("from_template_with_text"),
        target_attribute_spec_recipe=recipe_from_attribute_spec_mode(attr_mode),
        notes=note,
        signals=_plan_signals(env, patch_hint=patch_hint),
        planner="rule",
    )


def validate_compile_plan(plan: CompilePlan, env: PlanEnv) -> CompilePlan:
    mode = derive_input_mode(env)
    fixed = _fixed_plan_fields(mode)
    intro = plan.intro_enrich if plan.intro_enrich in ("skip", "llm_fill") else "skip"
    attr = plan.attribute_spec_mode
    if attr not in ("from_text", "from_template", "template_then_patch"):
        attr = "from_text"

    extract_recipe = normalize_target_extract_recipe(
        plan.target_extract_recipe or None,
        fallback_source=plan.target_extract_source,
    )
    attr_recipe = normalize_target_attribute_spec_recipe(
        plan.target_attribute_spec_recipe or None,
        fallback_mode=attr,
    )

    if mode == "text_requirement":
        intro = "llm_fill" if env.has_requirement else "skip"
        attr = "from_text"
    elif mode == "template":
        intro = "skip"
        attr = "from_template"
    else:
        if attr == "from_text":
            attr = "from_template"
        if not env.has_requirement:
            intro = "skip"
            attr = "from_template"

    extract_recipe = validate_target_extract_recipe(extract_recipe, input_mode=mode)
    attr_recipe = validate_target_attribute_spec_recipe(attr_recipe, input_mode=mode)
    extract_src = extract_source_from_recipe(extract_recipe)
    attr = attribute_spec_mode_from_recipe(attr_recipe)

    signals = dict(plan.signals or {})
    signals.update(_plan_signals(env, patch_hint=attr == "template_then_patch"))

    return CompilePlan(
        input_mode=mode,
        target_extract_source=extract_src,  # type: ignore[arg-type]
        intro_enrich=intro,  # type: ignore[arg-type]
        attribute_spec_mode=attr,  # type: ignore[arg-type]
        strategy=fixed["strategy"],
        target_extract_recipe=extract_recipe,
        target_attribute_spec_recipe=attr_recipe,
        target_element_loc_recipe=normalize_target_element_loc_recipe(plan.target_element_loc_recipe),
        document_modify_recipe=normalize_document_modify_recipe(plan.document_modify_recipe),
        notes=plan.notes,
        signals=signals,
        planner=plan.planner,
    )


def _render_prompt(template: str, env: PlanEnv, input_mode: InputMode) -> str:
    text = env.requirement.strip()
    if not text:
        placeholder = (
            _EMPTY_REQUIREMENT_PLACEHOLDER_EN
            if str(env.locale).lower().startswith("en")
            else _EMPTY_REQUIREMENT_PLACEHOLDER_ZH
        )
        text = placeholder
    return (
        template.replace("{{has_template}}", str(env.has_template).lower())
        .replace("{{has_requirement}}", str(env.has_requirement).lower())
        .replace("{{input_mode}}", input_mode)
        .replace("{{requirement_text}}", text)
    )


def _parse_recipe_list(parsed: dict[str, Any], key: str) -> list[dict[str, Any]] | None:
    raw = parsed.get(key)
    if isinstance(raw, list) and raw:
        return [step for step in raw if isinstance(step, dict) and step.get("op")]
    return None


def compile_plan_with_llm(
    env: PlanEnv,
    *,
    language_model: Any,
    prompt: dict[str, str],
    llm_kwargs: dict[str, Any] | None = None,
) -> CompilePlan | None:
    input_mode = derive_input_mode(env)
    system = str(prompt.get("system") or "")
    user = _render_prompt(str(prompt.get("user_template") or ""), env, input_mode)
    if not system.strip() or not user.strip():
        return None
    try:
        result = language_model.chat_json(system=system, user=user, **(llm_kwargs or {}))
    except Exception:
        return None
    parsed = parse_llm_json(result)
    if not parsed:
        return None

    rule_seed = compile_plan_rule(env)
    intro = str(parsed.get("intro_enrich") or rule_seed.intro_enrich)
    notes = str(parsed.get("notes") or "").strip() or rule_seed.notes

    extract_recipe = _parse_recipe_list(parsed, "target_extract_recipe")
    if not extract_recipe:
        extract_recipe = rule_seed.target_extract_recipe

    attr_recipe = _parse_recipe_list(parsed, "target_attribute_spec_recipe")
    if not attr_recipe:
        attr_recipe = _parse_recipe_list(parsed, "target_spec_recipe")
    if not attr_recipe:
        attr = str(parsed.get("attribute_spec_mode") or rule_seed.attribute_spec_mode)
        attr_recipe = recipe_from_attribute_spec_mode(attr)

    loc_recipe = _parse_recipe_list(parsed, "target_element_loc_recipe") or rule_seed.target_element_loc_recipe
    modify_recipe = _parse_recipe_list(parsed, "document_modify_recipe") or rule_seed.document_modify_recipe

    draft = CompilePlan(
        input_mode=input_mode,
        target_extract_source=rule_seed.target_extract_source,
        intro_enrich=intro if intro in ("skip", "llm_fill") else rule_seed.intro_enrich,  # type: ignore[arg-type]
        attribute_spec_mode=attribute_spec_mode_from_recipe(attr_recipe),  # type: ignore[arg-type]
        strategy=rule_seed.strategy,
        target_extract_recipe=extract_recipe,
        target_attribute_spec_recipe=attr_recipe,
        target_element_loc_recipe=loc_recipe,
        document_modify_recipe=modify_recipe,
        notes=notes,
        signals=dict(rule_seed.signals),
        planner="llm",
    )
    return validate_compile_plan(draft, env)


def build_compile_plan(
    env: PlanEnv,
    *,
    language_model: Any | None = None,
    prompt: dict[str, str] | None = None,
    llm_kwargs: dict[str, Any] | None = None,
    use_llm: bool = True,
) -> CompilePlan:
    if use_llm and language_model is not None and prompt:
        llm_plan = compile_plan_with_llm(
            env,
            language_model=language_model,
            prompt=prompt,
            llm_kwargs=llm_kwargs,
        )
        if llm_plan is not None:
            return llm_plan
    return validate_compile_plan(compile_plan_rule(env), env)


def plan_from_state(state: dict[str, Any]) -> CompilePlan:
    if isinstance(state.get("compile_plan"), dict):
        return CompilePlan.from_dict(state["compile_plan"])
    env = PlanEnv.from_state(state)
    return build_compile_plan(env, use_llm=False)


def compile_plan(
    *,
    input_mode: InputMode | str,
    requirement: str = "",
    has_template: bool = False,
    text_meta: dict[str, Any] | None = None,
) -> CompilePlan:
    del text_meta
    text = str(requirement or "").strip()
    env = PlanEnv(
        has_template=bool(has_template),
        has_requirement=bool(text),
        requirement=text,
    )
    mode = str(input_mode)
    if mode != derive_input_mode(env):
        env = PlanEnv(
            has_template=env.has_template or mode in ("template", "template_w_text"),
            has_requirement=env.has_requirement or mode in ("text_requirement", "template_w_text"),
            requirement=text,
        )
    return validate_compile_plan(compile_plan_rule(env), env)
