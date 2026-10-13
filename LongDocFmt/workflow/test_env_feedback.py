"""Guards for the env_feedback switch."""
from __future__ import annotations

from LongDocFmt.workflow.assignment import assign_sections
from LongDocFmt.workflow.contracts import Catalog, CatalogEntry, DocElement
from LongDocFmt.workflow.compile_plan import replan_without_template
from LongDocFmt.workflow.declarations import _illegal_field_report
from LongDocFmt.workflow.env_feedback import (
    classify_tool_error,
    feedback_enabled,
    format_stage_feedback,
    is_file_access_error,
)


def test_switch_defaults_off_and_reads_config():
    assert feedback_enabled({}) is False
    assert feedback_enabled({"env_feedback": {"enabled": False}}) is False
    assert feedback_enabled({"env_feedback": {"enabled": True}}) is True


def test_file_access_and_tool_error_classes():
    assert is_file_access_error(FileNotFoundError("template.docx"))
    assert is_file_access_error(RuntimeError("No such file or directory"))
    assert not is_file_access_error(RuntimeError("timeout"))
    assert classify_tool_error("unknown property fontSize") == "attribute"
    assert classify_tool_error("path /body/p[3] not found") == "location"
    assert (
        classify_tool_error("", {"command": "add", "type": "style", "props": {}})
        == "attribute"
    )


def test_stage_feedback_is_empty_for_other_kind():
    failures = [{"kind": "other", "command": "set", "path": "/x", "props": [], "message": "boom"}]
    assert format_stage_feedback(failures, "attribute") == ""
    assert format_stage_feedback(failures, "location") == ""
    text = format_stage_feedback(
        [{"kind": "attribute", "command": "set", "path": "/styles/A", "props": ["fontSize"], "message": "unknown property"}],
        "attribute",
    )
    assert "fontSize" in text
    assert "whitelist" in text


def test_illegal_declaration_fields_are_reported():
    parsed = {
        "declarations": [
            {"style_id": "ParaBody", "object": "paragraph.body", "props": {"size": 12, "fontSize": 12}},
        ]
    }
    report = _illegal_field_report(
        parsed,
        root_key="declarations",
        id_to_object={"ParaBody": "paragraph.body"},
    )
    assert "ParaBody" in report
    assert "fontSize" in report
    assert "not valid officecli keys" in report


def test_replan_without_template_is_text_recipe():
    plan = replan_without_template(
        requirement="Body text is 12 pt.",
        locale="en",
        reason="template.docx not found",
        use_llm=False,
    )
    assert plan.input_mode == "text_requirement"
    assert plan.attribute_spec_mode == "from_text"
    assert plan.target_extract_source == "from_text"
    assert "template.docx" in plan.notes
    assert plan.signals.get("env_feedback") == "template_file_access"


class _RetryLM:
    def __init__(self) -> None:
        self.calls = 0

    def chat_json(self, *, system, user, **kwargs):
        del system, kwargs
        self.calls += 1
        if "not valid JSON" not in user and "wrong structure" not in user:
            return {"content": "{not-json", "usage": {}}
        return {
            "content": '{"section":{"1":"SecBody"}}',
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }


def test_repair_masks_successful_locations_and_splits_routes():
    from LongDocFmt.workflow.apply_repair import merge_assignment, plan_repairs
    from LongDocFmt.workflow.contracts import Assignment, Catalog, CatalogEntry, DocElement

    catalog = Catalog(
        entries=[
            CatalogEntry("ParaBody", "paragraph.body", "Body", ""),
            CatalogEntry("ParaTitle", "paragraph.body", "Title", ""),
        ]
    )
    elements = {
        "paragraph.body": [
            DocElement("paragraph.body", 1, "/body/p[1]", {}, content="Kept"),
            DocElement("paragraph.body", 2, "/body/p[2]", {}, content="Missing"),
            DocElement("paragraph.body", 3, "/body/p[3]", {}, content=""),
        ]
    }
    loc = {
        "by_layer": {"paragraph.body": {"1": "ParaBody", "99": "ParaTitle"}},
        "table_cells": {},
        "paragraph_runs": {"1": [{"run_style": "RunCite"}]},
    }
    plan = plan_repairs(
        [
            {
                "command": "set",
                "path": "/styles/ParaTitle",
                "props": ["fontSize"],
                "message": "unknown property fontSize",
            },
            {
                "command": "set",
                "path": "/body/p[50]",
                "props": ["style"],
                "message": "path not found",
            },
        ],
        elements,
        loc,
        catalog,
    )
    assert plan.attribute_style_ids == ["ParaTitle"]
    assert plan.retry_ids["paragraph.body"] == ["2"]
    assert "1" in plan.frozen_ids["paragraph.body"]
    assert "3" not in plan.retry_ids["paragraph.body"]
    assert plan.dropped_fake["paragraph.body"] == ["99"]

    merged = merge_assignment(
        Assignment.from_dict(loc),
        Assignment.from_dict(
            {"by_layer": {"paragraph.body": {"1": "ParaTitle", "2": "ParaTitle"}}, "table_cells": {}, "paragraph_runs": {}}
        ),
        plan.retry_ids,
        elements,
    )
    assert merged.by_layer["paragraph.body"]["1"] == "ParaBody"
    assert merged.by_layer["paragraph.body"]["2"] == "ParaTitle"
    assert "99" not in merged.by_layer["paragraph.body"]
    assert merged.paragraph_runs["1"] == [{"run_style": "RunCite"}]


def test_command_feedback_keeps_only_rejected_paths():
    from LongDocFmt.workflow.command_feedback import (
        commands_for_paths,
        keep_repair_failure,
        parse_replacement_commands,
    )

    commands = [
        {"command": "set", "path": "/styles/ParaTitle", "props": {"fontSize": "16pt"}},
        {"command": "set", "path": "/body/p[1]", "props": {"style": "ParaBody"}},
    ]
    picked = commands_for_paths(commands, {"/styles/ParaTitle"})
    assert [item["path"] for item in picked] == ["/styles/ParaTitle"]
    assert keep_repair_failure({"path": "/styles/ParaTitle"}, {"/body/p[1]"})
    assert not keep_repair_failure({"path": "/body/p[50]"}, {"/body/p[1]"})
    revised = parse_replacement_commands(
        {"commands": [
            {"command": "set", "path": "/styles/ParaTitle", "props": {"size": "16pt"}},
            {"command": "set", "path": "/body/p[9]", "props": {"size": "12pt"}},
        ]},
        {"/styles/ParaTitle"},
    )
    assert revised == [{"command": "set", "path": "/styles/ParaTitle", "props": {"size": "16pt"}}]


def test_loc_json_retries_once_when_switch_is_on():
    catalog = Catalog(entries=[CatalogEntry("SecBody", "section", "Body", "")])
    elements = [DocElement("section", 1, "/section[1]", {}, content="Body")]
    prompt = {"system": "json", "user_template": "{{style_list_json}}\n{{elements_json}}"}
    quiet = _RetryLM()
    missed = assign_sections(
        catalog=catalog,
        elements=elements,
        language_model=quiet,
        prompt=prompt,
        llm_kwargs={},
        skip_if_single=False,
        cue_keys=[],
        cue_mode="contrastive",
        env_feedback=False,
    )
    assert missed == {}
    assert quiet.calls == 1

    retry = _RetryLM()
    mapped = assign_sections(
        catalog=catalog,
        elements=elements,
        language_model=retry,
        prompt=prompt,
        llm_kwargs={},
        skip_if_single=False,
        cue_keys=[],
        cue_mode="contrastive",
        env_feedback=True,
    )
    assert mapped == {"1": "SecBody"}
    assert retry.calls == 2
