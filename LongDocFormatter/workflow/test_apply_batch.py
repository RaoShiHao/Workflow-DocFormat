"""Unit tests for apply command compile + batch executor helpers."""

from __future__ import annotations

from LongDocFormatter.workflow.apply_core import merge_same_path, public_cmd, set_cmd
from LongDocFormatter.workflow.contracts import Assignment, Catalog, CatalogEntry
from LongDocFormatter.workflow.apply import collect_apply_commands


def test_set_cmd_encodes_bool_and_strips_empty() -> None:
    cmd = set_cmd("/body/p[1]", {"bold": True, "size": "12", "skip": None})
    assert cmd is not None
    assert cmd["command"] == "set"
    assert cmd["props"]["bold"] == "true"
    assert cmd["props"]["size"] == "12"
    assert "skip" not in cmd["props"]


def test_public_cmd_drops_internal_meta() -> None:
    cmd = set_cmd("/body/p[1]", {"style": "Para1"}, _soft_props={"style": "Para1"}, _alt_paths=["/x"])
    assert cmd is not None
    pub = public_cmd(cmd)
    assert "_soft_props" not in pub
    assert "_alt_paths" not in pub
    assert pub["props"]["style"] == "Para1"


def test_collect_merges_paragraph_style_and_instance_keys() -> None:
    catalog = Catalog(
        entries=[
            CatalogEntry(
                style_id="ParaBody",
                object="paragraph.body",
                display_name="Body",
                description="body",
            ),
        ]
    )
    declarations = {
        "ParaBody": {
            "object": "paragraph.body",
            "props": {"size": "12", "outlineLvl": 1, "keepNext": True},
        }
    }
    assignment = Assignment(by_layer={"paragraph.body": {"p1": "ParaBody"}})
    path_index = {("paragraph.body", "p1"): "/body/p[1]"}
    cmds = collect_apply_commands(
        catalog=catalog,
        declarations=declarations,
        assignment=assignment,
        path_index=path_index,
        init_elements={},
    )
    sets = [c for c in cmds if c.get("command") == "set" and c.get("path") == "/body/p[1]"]
    assert len(sets) == 1
    assert sets[0]["props"]["style"] == "ParaBody"
    assert "outlineLvl" in sets[0]["props"]


def test_merge_same_path_combines_consecutive_sets() -> None:
    a = set_cmd("/body/p[1]", {"style": "ParaBody"})
    b = set_cmd("/body/p[1]", {"outlineLvl": "1"})
    assert a is not None and b is not None
    merged = merge_same_path([a, b])
    assert len(merged) == 1
    assert merged[0]["props"]["style"] == "ParaBody"
    assert merged[0]["props"]["outlineLvl"] == "1"
