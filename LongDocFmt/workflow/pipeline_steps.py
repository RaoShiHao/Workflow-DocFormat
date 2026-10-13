"""Pipeline step ids, artifact dirs, and IR file names.

Core IR pipeline (four stages):
  03 target_extract          → target_set.json
  04 target_attribute_spec   → target_props.json
  05 target_element_loc      → target_loc.json
  06 document_modify         → modify_manifest.json (+ output path as modified_doc)

Infrastructure: 00 compile_plan, 01 prepare, 02 inventory, 07 finalize.
Legacy step folders and filenames remain readable via :func:`ir_path`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

# Infrastructure
STEP_COMPILE_PLAN = "00_compile_plan"
STEP_PREPARE = "01_prepare"
STEP_INVENTORY = "02_inventory"
STEP_FINALIZE = "07_finalize"

# Core four stages
STEP_TARGET_EXTRACT = "03_target_extract"
STEP_TARGET_ATTRIBUTE_SPEC = "04_target_attribute_spec"
STEP_TARGET_ELEMENT_LOC = "05_target_element_loc"
STEP_DOCUMENT_MODIFY = "06_document_modify"

# Legacy step folder ids (resume compatibility)
LEGACY_STEP_TARGET_SPEC = "04_target_spec"
LEGACY_STEP_ELEMENT_ASSIGN = "05_element_assign"
LEGACY_STEP_APPLY = "06_apply"

# Primary artifact filenames
FILE_COMPILE_PLAN = "compile_plan.json"
FILE_PREPARE = "prepare.json"
FILE_INVENTORY_INIT = "inventory_init.json"
FILE_TARGET_SET = "target_set.json"
FILE_TARGET_PROPS = "target_props.json"
FILE_TARGET_PROPS_BASE = "target_props_base.json"
FILE_TARGET_PROPS_PATCH = "target_props_patch.json"
FILE_TARGET_LOC = "target_loc.json"
FILE_MODIFY_MANIFEST = "modify_manifest.json"
FILE_RUN_SUMMARY = "run_summary.json"
FILE_PIPELINE_MANIFEST = "pipeline_manifest.json"
FILE_TARGET_ATTRIBUTE_SPEC_WARNINGS = "target_attribute_spec_warnings.json"

# Legacy artifact filenames
LEGACY_FILE_CATALOG = "catalog.json"
LEGACY_FILE_TARGET_ATTRIBUTES = "target_attributes.json"
LEGACY_FILE_DECLARATIONS = "declarations.json"
LEGACY_FILE_TARGET_ATTRIBUTES_BASE = "target_attributes_base.json"
LEGACY_FILE_TARGET_SPEC_PATCHES = "target_spec_patches.json"
LEGACY_FILE_ELEMENT_ASSIGNMENT = "element_assignment.json"
LEGACY_FILE_ASSIGNMENT = "assignment.json"
LEGACY_FILE_APPLY_DONE = "done.json"
LEGACY_FILE_TARGET_SPEC_WARNINGS = "target_spec_warnings.json"

# State keys (primary)
STATE_TARGET_SET = "target_set"
STATE_TARGET_PROPS = "target_props"
STATE_TARGET_LOC = "target_loc"
STATE_COMPILE_PLAN = "compile_plan"

# Legacy state aliases (kept in sync by sync_state_ir_aliases)
STATE_LEGACY_CATALOG = "catalog"
STATE_LEGACY_DECLARATIONS = "declarations"
STATE_LEGACY_ASSIGNMENT = "assignment"
STATE_LEGACY_TARGET_ATTRIBUTES = "target_attributes"
STATE_LEGACY_ELEMENT_ASSIGNMENT = "element_assignment"

# Backward-compatible aliases for in-flight refactors
STEP_TARGET_SPEC = STEP_TARGET_ATTRIBUTE_SPEC
STEP_ELEMENT_ASSIGN = STEP_TARGET_ELEMENT_LOC
STEP_APPLY = STEP_DOCUMENT_MODIFY
FILE_TARGET_ATTRIBUTES = FILE_TARGET_PROPS
FILE_TARGET_ATTRIBUTES_BASE = FILE_TARGET_PROPS_BASE
FILE_TARGET_SPEC_PATCHES = FILE_TARGET_PROPS_PATCH
FILE_ELEMENT_ASSIGNMENT = FILE_TARGET_LOC
FILE_APPLY_DONE = FILE_MODIFY_MANIFEST
STATE_TARGET_ATTRIBUTES = STATE_TARGET_PROPS
STATE_ELEMENT_ASSIGNMENT = STATE_TARGET_LOC

PIPELINE_STEPS: list[tuple[str, str, str]] = [
    (STEP_COMPILE_PLAN, FILE_COMPILE_PLAN, "Compile plan (composable recipes)"),
    (STEP_PREPARE, FILE_PREPARE, "Prepare I/O and environment signals"),
    (STEP_INVENTORY, FILE_INVENTORY_INIT, "Inventory source (+ template)"),
    (STEP_TARGET_EXTRACT, FILE_TARGET_SET, "Step 1 — target_extract → target_set"),
    (STEP_TARGET_ATTRIBUTE_SPEC, FILE_TARGET_PROPS, "Step 2 — target_attribute_spec → target_props"),
    (STEP_TARGET_ELEMENT_LOC, FILE_TARGET_LOC, "Step 3 — target_element_loc → target_loc"),
    (STEP_DOCUMENT_MODIFY, FILE_MODIFY_MANIFEST, "Step 4 — document_modify → modified_doc"),
    (STEP_FINALIZE, FILE_RUN_SUMMARY, "Run summary + pipeline manifest"),
]

_PRIMARY_TO_STEP: dict[str, str] = {
    FILE_COMPILE_PLAN: STEP_COMPILE_PLAN,
    FILE_PREPARE: STEP_PREPARE,
    FILE_INVENTORY_INIT: STEP_INVENTORY,
    FILE_TARGET_SET: STEP_TARGET_EXTRACT,
    FILE_TARGET_PROPS: STEP_TARGET_ATTRIBUTE_SPEC,
    FILE_TARGET_LOC: STEP_TARGET_ELEMENT_LOC,
    FILE_MODIFY_MANIFEST: STEP_DOCUMENT_MODIFY,
    FILE_RUN_SUMMARY: STEP_FINALIZE,
    FILE_PIPELINE_MANIFEST: STEP_FINALIZE,
    # legacy primary names → same steps
    LEGACY_FILE_TARGET_ATTRIBUTES: STEP_TARGET_ATTRIBUTE_SPEC,
    LEGACY_FILE_ELEMENT_ASSIGNMENT: STEP_TARGET_ELEMENT_LOC,
    LEGACY_FILE_APPLY_DONE: STEP_DOCUMENT_MODIFY,
}

_LEGACY_TO_STEP: dict[str, str] = {
    LEGACY_FILE_CATALOG: STEP_TARGET_EXTRACT,
    LEGACY_FILE_DECLARATIONS: STEP_TARGET_ATTRIBUTE_SPEC,
    LEGACY_FILE_ASSIGNMENT: STEP_TARGET_ELEMENT_LOC,
}

_FILE_ALIASES: dict[str, list[str]] = {
    FILE_TARGET_PROPS: [LEGACY_FILE_TARGET_ATTRIBUTES, LEGACY_FILE_DECLARATIONS],
    FILE_TARGET_PROPS_BASE: [LEGACY_FILE_TARGET_ATTRIBUTES_BASE],
    FILE_TARGET_PROPS_PATCH: [LEGACY_FILE_TARGET_SPEC_PATCHES],
    FILE_TARGET_LOC: [LEGACY_FILE_ELEMENT_ASSIGNMENT, LEGACY_FILE_ASSIGNMENT],
    FILE_MODIFY_MANIFEST: [LEGACY_FILE_APPLY_DONE],
    FILE_TARGET_ATTRIBUTE_SPEC_WARNINGS: [LEGACY_FILE_TARGET_SPEC_WARNINGS],
}

_STEP_ALIASES: dict[str, list[str]] = {
    STEP_TARGET_ATTRIBUTE_SPEC: [LEGACY_STEP_TARGET_SPEC],
    STEP_TARGET_ELEMENT_LOC: [LEGACY_STEP_ELEMENT_ASSIGN],
    STEP_DOCUMENT_MODIFY: [LEGACY_STEP_APPLY],
}


def step_dir(artifacts: Path, step: str) -> Path:
    return Path(artifacts) / step


def step_artifact_path(artifacts: Path, step: str, filename: str) -> Path:
    return step_dir(artifacts, step) / filename


def write_step_artifact(
    artifacts: Path,
    *,
    step: str,
    filename: str,
    payload: Any,
    save_json,
) -> Path:
    path = step_artifact_path(artifacts, step, filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    save_json(path, payload)
    return path


def _candidate_paths(artifacts: Path, primary: str, legacy: str | None) -> list[Path]:
    root = Path(artifacts)
    names = [primary, *(_FILE_ALIASES.get(primary) or [])]
    if legacy and legacy not in names:
        names.append(legacy)
    steps = []
    step = _PRIMARY_TO_STEP.get(primary) or _LEGACY_TO_STEP.get(legacy or "")
    if step:
        steps.append(step)
        steps.extend(_STEP_ALIASES.get(step, []))
    paths: list[Path] = []
    for st in steps:
        for name in names:
            paths.append(step_dir(root, st) / name)
    for name in names:
        paths.append(root / name)
    return paths


def ir_path(artifacts: Path, primary: str, legacy: str | None = None) -> Path:
    """Resolve artifact path: new step folder, legacy step folder, then artifacts root."""
    for path in _candidate_paths(artifacts, primary, legacy):
        if path.is_file():
            return path
    step = _PRIMARY_TO_STEP.get(primary)
    if step:
        return step_dir(artifacts, step) / primary
    return Path(artifacts) / primary


def write_ir_bundle(
    artifacts: Path,
    *,
    step: str,
    primary_name: str,
    legacy_name: str | None,
    payload: Any,
    save_json,
) -> None:
    write_step_artifact(artifacts, step=step, filename=primary_name, payload=payload, save_json=save_json)
    if legacy_name:
        write_step_artifact(artifacts, step=step, filename=legacy_name, payload=payload, save_json=save_json)
    for alias in _FILE_ALIASES.get(primary_name) or []:
        if alias != legacy_name:
            write_step_artifact(artifacts, step=step, filename=alias, payload=payload, save_json=save_json)


def build_pipeline_manifest(artifacts: Path) -> dict[str, Any]:
    root = Path(artifacts)
    steps: list[dict[str, Any]] = []
    legacy_pairs = {
        STEP_TARGET_EXTRACT: LEGACY_FILE_CATALOG,
        STEP_TARGET_ATTRIBUTE_SPEC: LEGACY_FILE_DECLARATIONS,
        STEP_TARGET_ELEMENT_LOC: LEGACY_FILE_ASSIGNMENT,
    }
    for step_id, filename, description in PIPELINE_STEPS:
        path = step_artifact_path(root, step_id, filename)
        entry: dict[str, Any] = {
            "step": step_id,
            "artifact": filename,
            "path": str(path.relative_to(root)).replace("\\", "/"),
            "description": description,
            "exists": path.is_file(),
        }
        leg = legacy_pairs.get(step_id)
        if leg:
            legacy = step_artifact_path(root, step_id, leg)
            entry["legacy_artifact"] = leg
            entry["legacy_exists"] = legacy.is_file()
        steps.append(entry)
    manifest_path = step_artifact_path(root, STEP_FINALIZE, FILE_PIPELINE_MANIFEST)
    return {
        "pipeline_order": [s["step"] for s in steps],
        "steps": steps,
        "plan_artifact": f"{STEP_COMPILE_PLAN}/{FILE_COMPILE_PLAN}",
        "manifest_path": str(manifest_path.relative_to(root)).replace("\\", "/"),
    }


def sync_state_ir_aliases(state: dict[str, Any]) -> dict[str, Any]:
    out = dict(state)
    if STATE_TARGET_SET in out:
        out[STATE_LEGACY_CATALOG] = out[STATE_TARGET_SET]
    elif STATE_LEGACY_CATALOG in out:
        out[STATE_TARGET_SET] = out[STATE_LEGACY_CATALOG]
    if STATE_TARGET_PROPS in out:
        out[STATE_LEGACY_DECLARATIONS] = out[STATE_TARGET_PROPS]
        out[STATE_LEGACY_TARGET_ATTRIBUTES] = out[STATE_TARGET_PROPS]
    elif STATE_LEGACY_DECLARATIONS in out:
        out[STATE_TARGET_PROPS] = out[STATE_LEGACY_DECLARATIONS]
    elif STATE_LEGACY_TARGET_ATTRIBUTES in out:
        out[STATE_TARGET_PROPS] = out[STATE_LEGACY_TARGET_ATTRIBUTES]
    if STATE_TARGET_LOC in out:
        out[STATE_LEGACY_ASSIGNMENT] = out[STATE_TARGET_LOC]
        out[STATE_LEGACY_ELEMENT_ASSIGNMENT] = out[STATE_TARGET_LOC]
    elif STATE_LEGACY_ASSIGNMENT in out:
        out[STATE_TARGET_LOC] = out[STATE_LEGACY_ASSIGNMENT]
    elif STATE_LEGACY_ELEMENT_ASSIGNMENT in out:
        out[STATE_TARGET_LOC] = out[STATE_LEGACY_ELEMENT_ASSIGNMENT]
    return out
