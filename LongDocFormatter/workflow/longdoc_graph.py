"""LangGraph: plan-and-execute over four IR stages + infrastructure.

Core stages:
  03 target_extract         → target_set.json
  04 target_attribute_spec  → target_props.json
  05 target_element_loc     → target_loc.json
  06 document_modify        → modify_manifest.json (modified_doc path)

Legacy state keys ``catalog`` / ``declarations`` / ``assignment`` stay in sync.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import yaml
from langgraph.graph import END, StateGraph

from LongDocFormatter.constants import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_PROMPT_DIR,
    PACKAGE_ROOT,
    REPO_ROOT,
)
from LongDocFormatter.workflow.document_modify_ops import execute_document_modify_recipe
from LongDocFormatter.workflow.target_element_loc_ops import execute_target_element_loc_recipe
from LongDocFormatter.workflow.compile_plan import (
    CompilePlan,
    PlanEnv,
    build_compile_plan,
    compile_plan,
    compile_plan_rule,
    derive_input_mode,
    plan_from_state,
    validate_compile_plan,
)
from LongDocFormatter.workflow.contracts import LAYER_ORDER, Assignment, Catalog, InputMode, Layer
from LongDocFormatter.workflow.image_extract import attach_image_files, extract_embedded_images
from LongDocFormatter.workflow.input_cache import ensure_embedded_images, ensure_inventory, inventory_live
from LongDocFormatter.workflow.json_util import LlmJsonParseError, write_json
from LongDocFormatter.workflow.llm_trace import CallLogger, LlmBudgetExceeded
from LongDocFormatter.workflow.bilingual_prompt_loader import PromptLoader
from LongDocFormatter.workflow.pipeline_steps import (
    FILE_COMPILE_PLAN,
    FILE_MODIFY_MANIFEST,
    FILE_PIPELINE_MANIFEST,
    FILE_RUN_SUMMARY,
    FILE_TARGET_ATTRIBUTE_SPEC_WARNINGS,
    FILE_TARGET_LOC,
    FILE_TARGET_PROPS,
    FILE_TARGET_PROPS_BASE,
    FILE_TARGET_PROPS_PATCH,
    FILE_TARGET_SET,
    LEGACY_FILE_ASSIGNMENT,
    LEGACY_FILE_CATALOG,
    LEGACY_FILE_DECLARATIONS,
    STATE_COMPILE_PLAN,
    STATE_LEGACY_ASSIGNMENT,
    STATE_LEGACY_CATALOG,
    STATE_LEGACY_DECLARATIONS,
    STATE_TARGET_LOC,
    STATE_TARGET_PROPS,
    STATE_TARGET_SET,
    STEP_COMPILE_PLAN,
    STEP_DOCUMENT_MODIFY,
    STEP_FINALIZE,
    STEP_INVENTORY,
    STEP_PREPARE,
    STEP_TARGET_ATTRIBUTE_SPEC,
    STEP_TARGET_ELEMENT_LOC,
    STEP_TARGET_EXTRACT,
    build_pipeline_manifest,
    ir_path,
    sync_state_ir_aliases,
    write_ir_bundle,
    write_step_artifact,
)
from LongDocFormatter.workflow.strategy import PipelineContext, build_target_props, build_target_set


def _load_yaml(path: str | Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _save_json(path: Path, data: Any) -> None:
    write_json(path, data)


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _nonempty(value: Any) -> bool:
    return bool(str(value or "").strip())


def _resolve_pkg_path(path: str | Path | None, default: Path) -> Path:
    if path is None:
        return default
    p = Path(path)
    if p.is_absolute():
        return p
    for base in (PACKAGE_ROOT, REPO_ROOT):
        cand = base / p
        if cand.exists():
            return cand
    return PACKAGE_ROOT / p


def normalize_io(state: Dict[str, Any]) -> Dict[str, Any]:
    """Canonical fields: ``template``, ``requirement``, ``source``, ``output``."""
    template = str(state.get("template") or state.get("template_doc_path") or "").strip()
    requirement = str(state.get("requirement") or state.get("text_input") or "").strip()
    source = str(
        state.get("source")
        or state.get("source_doc_path")
        or state.get("init_doc_path")
        or ""
    ).strip()
    output = str(state.get("output") or state.get("output_doc_path") or "").strip()
    if not source:
        raise ValueError("source is required (unformatted input .docx)")
    if not output:
        raise ValueError("output is required")
    if not template and not requirement:
        raise ValueError("at least one of template or requirement is required")
    return {
        **state,
        "template": template,
        "requirement": requirement,
        "source": source,
        "output": output,
        "template_doc_path": template,
        "text_input": requirement,
        "init_doc_path": source,
        "source_doc_path": source,
        "output_doc_path": output,
    }


def detect_input_mode(state: Dict[str, Any]) -> InputMode:
    has_t = _nonempty(state.get("template") or state.get("template_doc_path"))
    has_r = _nonempty(state.get("requirement") or state.get("text_input"))
    if not has_t and not has_r:
        raise ValueError("at least one of template or requirement is required")
    if has_t and has_r:
        return "template_w_text"
    if has_t:
        return "template"
    return "text_requirement"


class LongDocFormatter:
    """Fixed IR pipeline with a constrained compile plan (plan-and-execute)."""

    def __init__(
        self,
        *,
        language_model,
        multimodal_model=None,
        prompt_dir: str | Path | None = None,
        config_path: str | Path | None = None,
    ):
        pdir = _resolve_pkg_path(prompt_dir, DEFAULT_PROMPT_DIR)
        cpath = _resolve_pkg_path(config_path, DEFAULT_CONFIG_PATH)
        self.language_model = language_model
        self.multimodal_model = multimodal_model
        self.prompt_loader = PromptLoader(pdir)
        self.cfg = _load_yaml(cpath)
        self._tracer: CallLogger | None = None

    def build(self):
        cfg = self.cfg
        target_cfg = dict(cfg.get("target_extract") or cfg.get("catalog") or {})
        skip_single_catalog = bool(target_cfg.get("skip_llm_if_single", True))
        catalog_max_examples = int(target_cfg.get("max_cluster_examples") or 3)
        catalog_max_content_chars = int(target_cfg.get("max_content_chars") or 800)
        assign_cfg = dict(cfg.get("assignment") or cfg.get("element_assign") or {})
        skip_single_assign = bool(assign_cfg.get("skip_llm_if_single", True))
        llm_workers = int(assign_cfg.get("llm_workers") or 1)
        batch_sizes = dict(assign_cfg.get("batch_sizes") or {})
        init_cues = dict(assign_cfg.get("init_cues") or {})
        table_context_paragraphs = int(assign_cfg.get("table_context_paragraphs") or 2)
        image_context_paragraphs = int(assign_cfg.get("image_context_paragraphs") or 2)
        max_edge = int(cfg.get("images", {}).get("max_edge", 1024))
        enabled: List[Layer] = list(cfg.get("enabled_layers") or list(LAYER_ORDER))

        def locale_of(state: Dict[str, Any]) -> str:
            return str(state.get("locale") or cfg.get("locale") or "en")

        def set_step(name: str) -> None:
            if self._tracer is not None:
                self._tracer.set_step(name)

        def llm_kwargs(state: Dict[str, Any]) -> Dict[str, Any]:
            return dict(state.get("llm_kwargs") or {})

        def log(msg: str) -> None:
            print(f"[longdoc] {msg}")

        def prepare(state: Dict[str, Any]) -> Dict[str, Any]:
            log("prepare")
            set_step(STEP_PREPARE)
            state = normalize_io(state)
            artifacts = Path(state.get("artifacts_dir") or (Path(state["output"]).parent / "artifacts"))
            artifacts.mkdir(parents=True, exist_ok=True)
            mode = detect_input_mode(state)
            has_template = _nonempty(state.get("template"))
            has_requirement = _nonempty(state.get("requirement"))
            init_path = Path(state["source"])
            out_path = Path(state["output"])
            out_path.parent.mkdir(parents=True, exist_ok=True)

            cache_dir = Path(state["inventory_cache_dir"]) if state.get("inventory_cache_dir") else None
            if cache_dir:
                cache_dir.mkdir(parents=True, exist_ok=True)
                init_manifest = ensure_embedded_images(
                    init_path, cache_dir, stem="source", max_edge=max_edge
                )
            else:
                init_manifest = extract_embedded_images(
                    init_path, artifacts / "init_images", max_edge=max_edge
                )

            template_manifest: List[Dict[str, Any]] = []
            if state.get("template"):
                if cache_dir:
                    template_manifest = ensure_embedded_images(
                        Path(state["template"]), cache_dir, stem="template", max_edge=max_edge
                    )
                else:
                    template_manifest = extract_embedded_images(
                        Path(state["template"]),
                        artifacts / "template_images",
                        max_edge=max_edge,
                    )
            write_step_artifact(
                artifacts,
                step=STEP_PREPARE,
                filename="prepare.json",
                payload={
                    "input_mode": mode,
                    "has_template": has_template,
                    "has_requirement": has_requirement,
                    "template": state.get("template") or "",
                    "source": str(init_path),
                    "output": str(out_path),
                    "locale": locale_of(state),
                },
                save_json=_save_json,
            )
            empty_loc = {"by_layer": {}, "table_cells": {}, "paragraph_runs": {}}
            return sync_state_ir_aliases({
                **state,
                "artifacts_dir": str(artifacts),
                "input_mode": mode,
                "has_template": has_template,
                "has_requirement": has_requirement,
                "init_image_manifest": init_manifest,
                "template_image_manifest": template_manifest,
                STATE_TARGET_SET: Catalog().to_dict(),
                STATE_TARGET_PROPS: {},
                STATE_TARGET_LOC: empty_loc,
            })

        compile_cfg = dict(cfg.get("compile_plan") or {})
        use_llm_planner = bool(compile_cfg.get("use_llm", True))

        def compile_plan_node(state: Dict[str, Any]) -> Dict[str, Any]:
            log("compile_plan")
            set_step(STEP_COMPILE_PLAN)
            artifacts = Path(state["artifacts_dir"])
            plan_path = ir_path(artifacts, FILE_COMPILE_PLAN, None)
            if plan_path.is_file():
                log("compile_plan (resume)")
                plan = CompilePlan.from_dict(_load_json(plan_path))
            else:
                env = PlanEnv.from_state(state)
                plan_prompt = self.prompt_loader.load(
                    "compile_plan", locale=locale_of(state), section="compile_plan"
                )
                try:
                    plan = build_compile_plan(
                        env,
                        language_model=self.language_model,
                        prompt=plan_prompt,
                        llm_kwargs=llm_kwargs(state),
                        use_llm=use_llm_planner,
                    )
                except LlmBudgetExceeded:
                    log("compile_plan (budget exhausted; rule fallback)")
                    plan = build_compile_plan(env, use_llm=False)
                if plan.planner == "rule":
                    log("compile_plan (rule fallback)")
                else:
                    log("compile_plan (llm)")
                payload = plan.to_dict()
                write_ir_bundle(
                    artifacts,
                    step=STEP_COMPILE_PLAN,
                    primary_name=FILE_COMPILE_PLAN,
                    legacy_name=None,
                    payload=payload,
                    save_json=_save_json,
                )
            return {**state, STATE_COMPILE_PLAN: plan.to_dict(), "strategy": plan.strategy}

        def inventory_docs(state: Dict[str, Any]) -> Dict[str, Any]:
            log("inventory")
            set_step(STEP_INVENTORY)
            cache_dir = Path(state["inventory_cache_dir"]) if state.get("inventory_cache_dir") else None
            init_path = Path(state["init_doc_path"])
            if cache_dir:
                init_el = ensure_inventory(init_path, cache_dir, stem="source")
            else:
                init_el = inventory_live(init_path, stem="source")
            attach_image_files(init_el.get("image") or [], state.get("init_image_manifest") or [])
            template_el = {}
            if state.get("template_doc_path"):
                tpath = Path(state["template_doc_path"])
                if cache_dir:
                    template_el = ensure_inventory(tpath, cache_dir, stem="template")
                else:
                    template_el = inventory_live(tpath, stem="template")
                attach_image_files(template_el.get("image") or [], state.get("template_image_manifest") or [])
            artifacts = Path(state["artifacts_dir"])
            slim = {
                k: [{"location_id": e.location_id, "path": e.path, "content": e.content} for e in v]
                for k, v in init_el.items()
            }
            write_step_artifact(
                artifacts,
                step=STEP_INVENTORY,
                filename="inventory_init.json",
                payload=slim,
                save_json=_save_json,
            )
            return {**state, "_init_elements": init_el, "_template_elements": template_el}

        def _pipeline_ctx(state: Dict[str, Any]) -> PipelineContext:
            plan = plan_from_state(state)
            return PipelineContext(
                language_model=self.language_model,
                prompt_loader=self.prompt_loader,
                enabled_layers=enabled,
                skip_single_catalog=skip_single_catalog,
                locale=locale_of(state),
                llm_kwargs=llm_kwargs(state),
                catalog_max_examples=catalog_max_examples,
                catalog_max_content_chars=catalog_max_content_chars,
                compile_plan=plan,
                target_attribute_spec_warnings=[],
            )

        def extract_targets(state: Dict[str, Any]) -> Dict[str, Any]:
            log("target_extract")
            set_step(STEP_TARGET_EXTRACT)
            artifacts = Path(state["artifacts_dir"])
            cat_path = ir_path(artifacts, FILE_TARGET_SET, LEGACY_FILE_CATALOG)
            if cat_path.is_file():
                log("target_extract (resume)")
                payload = _load_json(cat_path)
                return sync_state_ir_aliases({
                    **state,
                    STATE_TARGET_SET: payload,
                    "strategy": plan_from_state(state).strategy,
                })
            try:
                catalog = build_target_set(state, ctx=_pipeline_ctx(state))
            except LlmBudgetExceeded:
                log("target_extract (budget exhausted before/during extract)")
                catalog = Catalog()
            except LlmJsonParseError as err:
                log(f"target_extract (JSON parse failed; keep geometry/empty catalog)")
                if self._tracer is not None:
                    self._tracer.note_parse_failure(
                        layer=err.layer, message=str(err), raw=err.raw
                    )
                catalog = Catalog()
            payload = catalog.to_dict()
            write_ir_bundle(
                artifacts,
                step=STEP_TARGET_EXTRACT,
                primary_name=FILE_TARGET_SET,
                legacy_name=LEGACY_FILE_CATALOG,
                payload=payload,
                save_json=_save_json,
            )
            return sync_state_ir_aliases({
                **state,
                STATE_TARGET_SET: payload,
                "strategy": plan_from_state(state).strategy,
            })

        def target_attribute_spec(state: Dict[str, Any]) -> Dict[str, Any]:
            log("target_attribute_spec")
            set_step(STEP_TARGET_ATTRIBUTE_SPEC)
            artifacts = Path(state["artifacts_dir"])
            props_path = ir_path(artifacts, FILE_TARGET_PROPS, LEGACY_FILE_DECLARATIONS)
            if props_path.is_file():
                target_props = _load_json(props_path)
                from LongDocFormatter.workflow.declarations import declarations_miss_template_outline

                if not declarations_miss_template_outline(
                    target_props, state.get("_template_elements") or {}
                ):
                    log("target_attribute_spec (resume)")
                    return sync_state_ir_aliases({
                        **state,
                        STATE_TARGET_PROPS: target_props,
                        "strategy": plan_from_state(state).strategy,
                    })
                log("target_attribute_spec (rebuild missing style-inherited keys)")
            catalog = Catalog.from_dict(state.get(STATE_TARGET_SET) or state.get(STATE_LEGACY_CATALOG))
            ctx = _pipeline_ctx(state)
            try:
                target_props = build_target_props(state, catalog, ctx=ctx)
            except LlmBudgetExceeded:
                log("target_attribute_spec (budget exhausted; keep partial/empty props)")
                ws = ctx.target_props_workspace
                target_props = (ws.result if ws and ws.result else {}) or {}
            ws = ctx.target_props_workspace
            if ws and ws.base:
                write_step_artifact(
                    artifacts,
                    step=STEP_TARGET_ATTRIBUTE_SPEC,
                    filename=FILE_TARGET_PROPS_BASE,
                    payload=ws.base,
                    save_json=_save_json,
                )
            if ws and ws.patch:
                write_step_artifact(
                    artifacts,
                    step=STEP_TARGET_ATTRIBUTE_SPEC,
                    filename=FILE_TARGET_PROPS_PATCH,
                    payload=ws.patch,
                    save_json=_save_json,
                )
            write_ir_bundle(
                artifacts,
                step=STEP_TARGET_ATTRIBUTE_SPEC,
                primary_name=FILE_TARGET_PROPS,
                legacy_name=LEGACY_FILE_DECLARATIONS,
                payload=target_props,
                save_json=_save_json,
            )
            if ctx.target_attribute_spec_warnings:
                write_step_artifact(
                    artifacts,
                    step=STEP_TARGET_ATTRIBUTE_SPEC,
                    filename=FILE_TARGET_ATTRIBUTE_SPEC_WARNINGS,
                    payload={
                        "step": STEP_TARGET_ATTRIBUTE_SPEC,
                        "warnings": ctx.target_attribute_spec_warnings,
                    },
                    save_json=_save_json,
                )
            return sync_state_ir_aliases({
                **state,
                STATE_TARGET_PROPS: target_props,
                "strategy": plan_from_state(state).strategy,
            })

        def target_element_loc(state: Dict[str, Any]) -> Dict[str, Any]:
            log("target_element_loc")
            set_step(STEP_TARGET_ELEMENT_LOC)
            artifacts = Path(state["artifacts_dir"])
            loc_path = ir_path(artifacts, FILE_TARGET_LOC, LEGACY_FILE_ASSIGNMENT)
            if loc_path.is_file():
                log("target_element_loc (resume)")
                payload = _load_json(loc_path)
                return sync_state_ir_aliases({**state, STATE_TARGET_LOC: payload})
            loc = locale_of(state)
            catalog = Catalog.from_dict(state.get(STATE_TARGET_SET) or state.get(STATE_LEGACY_CATALOG))
            para_runs_prompt = self.prompt_loader.load(
                "paragraph_with_runs", locale=loc, section="element_classification"
            )
            if not (para_runs_prompt.get("user_template") or para_runs_prompt.get("system")):
                para_runs_prompt = self.prompt_loader.load(
                    "paragraph_body", locale=loc, section="element_classification"
                )
            table_joint = self.prompt_loader.load(
                "table", locale=loc, section="element_classification_joint"
            )
            if not (table_joint.get("user_template") or table_joint.get("system")):
                table_joint = self.prompt_loader.load("table", locale=loc, section="element_classification")
            prompts: Dict[str, Dict[str, str]] = {
                "section": self.prompt_loader.load("section", locale=loc, section="element_classification"),
                "paragraph_with_runs": para_runs_prompt,
                "table_joint": table_joint,
                "image": self.prompt_loader.load("image", locale=loc, section="element_classification"),
            }
            plan = plan_from_state(state)
            try:
                assignment = execute_target_element_loc_recipe(
                    plan.target_element_loc_recipe,
                    catalog=catalog,
                    init_elements=state.get("_init_elements") or {},
                    target_props=state.get(STATE_TARGET_PROPS) or state.get(STATE_LEGACY_DECLARATIONS) or {},
                    language_model=self.language_model,
                    prompts=prompts,
                    llm_kwargs=llm_kwargs(state),
                    batch_sizes=batch_sizes,
                    skip_if_single=skip_single_assign,
                    multimodal_model=self.multimodal_model,
                    mm_kwargs=dict(state.get("mm_kwargs") or llm_kwargs(state)),
                    llm_workers=int(state.get("llm_workers") or llm_workers),
                    init_cues=init_cues,
                    table_context_paragraphs=table_context_paragraphs,
                    image_context_paragraphs=image_context_paragraphs,
                )
            except LlmBudgetExceeded:
                log("target_element_loc (budget exhausted; use empty assignment)")
                assignment = Assignment()
            except LlmJsonParseError as err:
                log(
                    f"target_element_loc (JSON parse failed on {err.layer}; "
                    "keep partial assignment and continue)"
                )
                if self._tracer is not None:
                    self._tracer.note_parse_failure(
                        layer=err.layer, message=str(err), raw=err.raw
                    )
                assignment = Assignment()
            payload = assignment.to_dict()
            write_ir_bundle(
                artifacts,
                step=STEP_TARGET_ELEMENT_LOC,
                primary_name=FILE_TARGET_LOC,
                legacy_name=LEGACY_FILE_ASSIGNMENT,
                payload=payload,
                save_json=_save_json,
            )
            return sync_state_ir_aliases({**state, STATE_TARGET_LOC: payload})

        def document_modify(state: Dict[str, Any]) -> Dict[str, Any]:
            log("document_modify")
            set_step(STEP_DOCUMENT_MODIFY)
            out = Path(state["output_doc_path"])
            manifest_path = ir_path(artifacts := Path(state["artifacts_dir"]), FILE_MODIFY_MANIFEST)
            if manifest_path.is_file() and out.is_file():
                log("document_modify (resume)")
                return state

            catalog = Catalog.from_dict(state.get(STATE_TARGET_SET) or state.get(STATE_LEGACY_CATALOG))
            assignment = Assignment.from_dict(
                state.get(STATE_TARGET_LOC) or state.get(STATE_LEGACY_ASSIGNMENT)
            )
            if assignment.is_empty():
                log("document_modify (skip empty assignment)")
                out.parent.mkdir(parents=True, exist_ok=True)
                init_doc = Path(state["init_doc_path"])
                if out.resolve() != init_doc.resolve():
                    import shutil

                    shutil.copy2(init_doc, out)
                write_step_artifact(
                    artifacts,
                    step=STEP_DOCUMENT_MODIFY,
                    filename=FILE_MODIFY_MANIFEST,
                    payload={
                        "modified_doc": str(out),
                        "ok": True,
                        "skipped": True,
                        "reason": "empty_assignment",
                        "budget_exhausted": bool(
                            self._tracer and self._tracer.is_budget_exhausted
                        ),
                    },
                    save_json=_save_json,
                )
                return state

            plan = plan_from_state(state)
            execute_document_modify_recipe(
                plan.document_modify_recipe,
                init_doc=Path(state["init_doc_path"]),
                output_doc=out,
                catalog=catalog,
                target_props=state.get(STATE_TARGET_PROPS) or state.get(STATE_LEGACY_DECLARATIONS) or {},
                assignment=assignment,
                init_elements=state.get("_init_elements") or {},
            )
            write_step_artifact(
                artifacts,
                step=STEP_DOCUMENT_MODIFY,
                filename=FILE_MODIFY_MANIFEST,
                payload={
                    "modified_doc": str(out),
                    "ok": True,
                    "skipped": False,
                    "budget_exhausted": bool(
                        self._tracer and self._tracer.is_budget_exhausted
                    ),
                },
                save_json=_save_json,
            )
            return state

        def finalize(state: Dict[str, Any]) -> Dict[str, Any]:
            log("finalize")
            set_step(STEP_FINALIZE)
            plan = plan_from_state(state)
            artifacts = Path(state["artifacts_dir"])
            pipeline_manifest = build_pipeline_manifest(artifacts)
            llm_usage = {}
            if self._tracer is not None:
                self._tracer.flush_summary()
                usage_path = self._tracer.usage_path
                if usage_path.is_file():
                    try:
                        llm_usage = json.loads(usage_path.read_text(encoding="utf-8"))
                    except json.JSONDecodeError:
                        llm_usage = {}
            summary = {
                "input_mode": state.get("input_mode"),
                "strategy": state.get("strategy"),
                "compile_plan": plan.to_dict(),
                "pipeline": pipeline_manifest,
                "llm_usage": llm_usage,
                "template": state.get("template"),
                "requirement": state.get("requirement"),
                "source": state.get("source"),
                "output": state.get("output"),
                "target_set": state.get(STATE_TARGET_SET),
            }
            write_step_artifact(
                artifacts,
                step=STEP_FINALIZE,
                filename=FILE_RUN_SUMMARY,
                payload=summary,
                save_json=_save_json,
            )
            write_step_artifact(
                artifacts,
                step=STEP_FINALIZE,
                filename=FILE_PIPELINE_MANIFEST,
                payload=pipeline_manifest,
                save_json=_save_json,
            )
            return state

        graph = StateGraph(dict)
        graph.add_node("prepare", prepare)
        graph.add_node("compile_plan", compile_plan_node)
        graph.add_node("inventory_docs", inventory_docs)
        graph.add_node("extract_targets", extract_targets)
        graph.add_node("target_attribute_spec", target_attribute_spec)
        graph.add_node("target_element_loc", target_element_loc)
        graph.add_node("document_modify", document_modify)
        graph.add_node("finalize", finalize)
        graph.set_entry_point("prepare")
        graph.add_edge("prepare", "compile_plan")
        graph.add_edge("compile_plan", "inventory_docs")
        graph.add_edge("inventory_docs", "extract_targets")
        graph.add_edge("extract_targets", "target_attribute_spec")
        graph.add_edge("target_attribute_spec", "target_element_loc")
        graph.add_edge("target_element_loc", "document_modify")
        graph.add_edge("document_modify", "finalize")
        graph.add_edge("finalize", END)
        return graph.compile()

    def run(self, state: Dict[str, Any] | None = None, **kwargs: Any) -> Dict[str, Any]:
        """Invoke the graph. Pass ``template`` / ``requirement`` / ``source`` / ``output``."""
        payload = dict(state or {})
        payload.update(kwargs)
        payload = normalize_io(payload)
        artifacts = Path(payload.get("artifacts_dir") or (Path(payload["output"]).parent / "artifacts"))
        artifacts.mkdir(parents=True, exist_ok=True)
        payload["artifacts_dir"] = str(artifacts)
        reuse = bool((self.cfg.get("llm_cache") or {}).get("use_cache", True))
        save_by_hash = bool((self.cfg.get("llm_cache") or {}).get("save_by_hash", True))
        # Cache hits count toward budget by default (fair resume vs cold run).
        count_cache = (self.cfg.get("llm_cache") or {}).get("count_cache_toward_budget")
        if count_cache is None:
            count_cache = True
        raw_max = self.cfg.get("max_llm_step", 50)
        max_llm_step = None if raw_max is None else int(raw_max)
        tracer = CallLogger(
            artifacts,
            reuse=reuse,
            save_by_hash=save_by_hash,
            max_llm_step=max_llm_step,
            count_cache_toward_budget=bool(count_cache),
        )
        prev_lm, prev_mm = self.language_model, self.multimodal_model
        self.language_model = tracer.wrap(prev_lm)
        self.multimodal_model = tracer.wrap(prev_mm) if prev_mm is not None else None
        self._tracer = tracer
        try:
            result = self.build().invoke(payload)
            tracer.flush_summary()
            return sync_state_ir_aliases(result)
        finally:
            self.language_model = prev_lm
            self.multimodal_model = prev_mm
            self._tracer = None
