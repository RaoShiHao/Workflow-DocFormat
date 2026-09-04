from __future__ import annotations

from pathlib import Path

from LongDocFormatter.evaluation.metrics import aggregate_accuracy_reports, failed_sample_metrics
from LongDocFormatter.workflow.assignment import build_assignment
from LongDocFormatter.workflow.contracts import Catalog, CatalogEntry, DocElement
from LongDocFormatter.workflow.llm_trace import CallLogger, TracingModel


class _LayerLM:
    """Good JSON for every layer except table, which returns truncated JSON."""

    model = "fake"

    def chat_json(self, *, system, user, **kwargs):
        if "{{cell_slots_by_table_json}}" not in user:
            return {
                "content": '{"tables":[{"location_id":1,"table_style":"TblGrid","cells":[',
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        if "{{run_styles_json}}" not in user:
            return {
                "content": '{"assignments":[{"location_id":1,"paragraph_style":"ParaBody"}]}',
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        if "picture_id" in user:
            return {
                "content": '{"assignments":[{"location_id":1,"style_id":"ImgBody"}]}',
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        return {
            "content": '{"assignments":[{"location_id":1,"style_id":"SecBody"}]}',
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }


def _prompt() -> dict[str, str]:
    return {
        "system": "json",
        "user_template": (
            "styles={{style_list_json}} {{table_styles_json}} {{paragraph_styles_json}} "
            "{{run_styles_json}} {{cell_slots_json}} {{cell_slots_by_table_json}} "
            "els={{elements_json}} outline={{outline_json}}"
        ),
    }


def test_table_parse_failure_does_not_block_later_layers(tmp_path: Path):
    logger = CallLogger(tmp_path, reuse=False, max_llm_step=20)
    logger.set_step("05_target_element_loc")
    lm = TracingModel(_LayerLM(), logger)
    catalog = Catalog(
        entries=[
            CatalogEntry("SecCover", "section", "Cover", ""),
            CatalogEntry("SecBody", "section", "Body", ""),
            CatalogEntry("ParaTitle", "paragraph.body", "Title", ""),
            CatalogEntry("ParaBody", "paragraph.body", "Body", ""),
            CatalogEntry("ParaCell", "paragraph.table_cell", "Cell", ""),
            CatalogEntry("TblOpen", "table", "Open", ""),
            CatalogEntry("TblGrid", "table", "Grid", ""),
            CatalogEntry("ImgCover", "image", "Cover", ""),
            CatalogEntry("ImgBody", "image", "Body", ""),
        ]
    )
    init = {
        "section": [DocElement("section", 1, "/section[1]", {}, content="Body")],
        "paragraph.body": [DocElement("paragraph.body", 1, "/body/p[1]", {}, content="Hello")],
        "table": [
            DocElement(
                "table",
                1,
                "/body/tbl[1]",
                {},
                meta={"n_rows": 1, "n_cols": 1, "cells": [{"row": 1, "col": 1, "text": "A"}]},
            )
        ],
        "image": [DocElement("image", 1, "/body/drawing[1]", {}, content="fig")],
    }
    prompts = {
        "section": _prompt(),
        "paragraph_with_runs": _prompt(),
        "table_joint": _prompt(),
        "image": _prompt(),
    }
    assignment = build_assignment(
        catalog=catalog,
        init_elements=init,
        language_model=lm,
        prompts=prompts,
        llm_kwargs={},
        batch_sizes={"section": None, "paragraph.body": None, "table": None, "image": None},
        skip_if_single=False,
        llm_workers=1,
        declarations={"TblGrid": {"cells": {"header": {}, "data": {}}}},
    )
    assert assignment.by_layer.get("section") == {"1": "SecBody"}
    assert assignment.by_layer.get("paragraph.body") == {"1": "ParaBody"}
    assert not assignment.by_layer.get("table")
    assert assignment.by_layer.get("image") == {"1": "ImgBody"}
    usage = (tmp_path / "llm" / "usage.json").read_text(encoding="utf-8")
    assert '"parse_failed_calls": 1' in usage or '"parse_failed_calls":1' in usage
    fails = (tmp_path / "llm" / "failures.json").read_text(encoding="utf-8")
    assert "table" in fails


def test_failed_sample_counts_as_zero_not_null():
    report = failed_sample_metrics(sample_dir="D:/LongDocForm/14", reason="no_output")
    payload = report.to_dict()
    assert payload["integrity_status"] == "failed"
    assert payload["metrics"]["accuracy"] == 0.0
    agg = aggregate_accuracy_reports([payload])
    assert agg["n_samples"] == 1
    assert agg["n_failed"] == 1
    assert agg["accuracy"] == 0.0
    assert agg["accuracy_by_mode"]["table"] == 0.0
