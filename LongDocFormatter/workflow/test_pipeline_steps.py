"""Tests for step-scoped artifact layout."""

from __future__ import annotations

import json
from pathlib import Path

from LongDocFormatter.workflow.json_util import write_json
from LongDocFormatter.workflow.pipeline_steps import (
    FILE_TARGET_SET,
    STEP_TARGET_EXTRACT,
    build_pipeline_manifest,
    ir_path,
    write_ir_bundle,
)


def test_ir_path_prefers_step_folder(tmp_path: Path):
    root = tmp_path / "artifacts"
    step_file = root / STEP_TARGET_EXTRACT / FILE_TARGET_SET
    step_file.parent.mkdir(parents=True)
    write_json(step_file, {"entries": []})
    write_json(root / FILE_TARGET_SET, {"entries": [{"legacy": True}]})
    loaded = json.loads(ir_path(root, FILE_TARGET_SET).read_text(encoding="utf-8"))
    assert loaded == {"entries": []}


def test_write_ir_bundle_step_only(tmp_path: Path):
    root = tmp_path / "artifacts"
    write_ir_bundle(
        root,
        step=STEP_TARGET_EXTRACT,
        primary_name=FILE_TARGET_SET,
        legacy_name="catalog.json",
        payload={"entries": []},
        save_json=write_json,
    )
    assert (root / STEP_TARGET_EXTRACT / FILE_TARGET_SET).is_file()
    assert (root / STEP_TARGET_EXTRACT / "catalog.json").is_file()
    assert not (root / FILE_TARGET_SET).is_file()


def test_pipeline_manifest_lists_steps(tmp_path: Path):
    root = tmp_path / "artifacts"
    (root / STEP_TARGET_EXTRACT).mkdir(parents=True)
    write_json(root / STEP_TARGET_EXTRACT / FILE_TARGET_SET, {"entries": []})
    manifest = build_pipeline_manifest(root)
    assert manifest["plan_artifact"] == "00_compile_plan/compile_plan.json"
    assert any(s["step"] == STEP_TARGET_EXTRACT and s["exists"] for s in manifest["steps"])
