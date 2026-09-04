from __future__ import annotations

from xml.etree import ElementTree as ET

from LongDocFormatter.officecli.read._ooxml_section import table_section_indices_from_body
from LongDocFormatter.workflow.catalog import (
    header_row,
    induce_catalog_from_all_layers,
    pick_caption,
)
from LongDocFormatter.workflow.contracts import DocElement
from LongDocFormatter.workflow.assignment import _catalog_brief, _nonempty_previews


_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _p(*, sect: bool = False) -> ET.Element:
    p = ET.Element(f"{{{_W}}}p")
    if sect:
        p_pr = ET.SubElement(p, f"{{{_W}}}pPr")
        ET.SubElement(p_pr, f"{{{_W}}}sectPr")
    return p


def test_table_after_section_break_is_next_section():
    body = ET.Element(f"{{{_W}}}body")
    body.append(_p(sect=True))
    body.append(ET.Element(f"{{{_W}}}tbl"))
    body.append(_p())
    ET.SubElement(body, f"{{{_W}}}sectPr")
    assert table_section_indices_from_body(body) == (2,)


def test_two_tables_same_section_then_next():
    body = ET.Element(f"{{{_W}}}body")
    body.append(_p())
    body.append(ET.Element(f"{{{_W}}}tbl"))
    body.append(_p(sect=True))
    body.append(ET.Element(f"{{{_W}}}tbl"))
    assert table_section_indices_from_body(body) == (1, 2)


def test_joint_naming_binds_table_to_section_role():
    sections = [
        [
            DocElement(layer="section", location_id=1, path="/section[1]", props={}, content="Cover"),
        ],
        [
            DocElement(layer="section", location_id=2, path="/section[2]", props={}, content="Findings"),
        ],
    ]
    tables = [
        [
            DocElement(
                layer="table",
                location_id=1,
                path="/body/tbl[1]",
                props={"table_format": {}},
                content="Finding | Risk",
                meta={
                    "section_index": 2,
                    "n_rows": 2,
                    "n_cols": 2,
                    "cells": [
                        {"row": 1, "col": 1, "text": "Finding"},
                        {"row": 1, "col": 2, "text": "Risk"},
                        {"row": 2, "col": 1, "text": "Access"},
                        {"row": 2, "col": 2, "text": "High"},
                    ],
                    "neighbor_after": [
                        {"location_id": 40, "content": "Table 1. Deficiency summary by process"}
                    ],
                },
            )
        ]
    ]

    class LM:
        def chat_json(self, *, system, user, **kwargs):
            assert "section_cluster_ids" in user
            assert "captions" in user
            assert "header_rows" in user
            assert "Finding | Risk" in user
            assert "Table 1. Deficiency summary" in user
            return {
                "styles": [
                    {
                        "object": "section",
                        "cluster_id": 1,
                        "style_name": "cover",
                        "display_name": "Cover",
                        "description": "Title page",
                    },
                    {
                        "object": "section",
                        "cluster_id": 2,
                        "style_name": "findings",
                        "display_name": "Findings",
                        "description": "Findings chapter",
                    },
                    {
                        "object": "table",
                        "cluster_id": 1,
                        "style_name": "minimalist",
                        "display_name": "Open table",
                        "description": "Finding-summary open tables",
                        "typical_section_cluster_ids": [2],
                        "caption_type": "finding-summary",
                        "header_semantics": "Finding | Risk",
                    },
                ]
            }

    prompt = {
        "system": "name",
        "user_template": "Clusters:\n{{layers_json}}\nNotes:\n{{requirement}}",
    }
    catalog, decls = induce_catalog_from_all_layers(
        clusters_by_layer={"section": sections, "table": tables},
        language_model=LM(),
        prompt=prompt,
        llm_kwargs={},
        requirement="",
    )
    tbl = next(e for e in catalog.entries if e.object == "table")
    assert tbl.style_id == "TblMinimalist"
    assert tbl.typical_sections == ["SecFindings"]
    assert tbl.caption_type == "finding-summary"
    assert tbl.header_semantics == "Finding | Risk"
    assert tbl.captions == ["Table 1. Deficiency summary by process"]
    assert tbl.header_rows == ["Finding | Risk"]
    assert decls[tbl.style_id]["typical_sections"] == ["SecFindings"]
    assert decls[tbl.style_id]["caption_type"] == "finding-summary"
    brief = _catalog_brief([tbl])
    assert brief[0]["typical_sections"] == ["SecFindings"]
    assert brief[0]["caption_type"] == "finding-summary"
    assert brief[0]["header_rows"] == ["Finding | Risk"]


def test_nonempty_previews_skip_blank():
    rows = [
        {"location_id": 1, "content": "   "},
        {"location_id": 2, "content": "Table 1. Deficiency summary"},
        {"location_id": 3, "content": ""},
    ]
    assert _nonempty_previews(rows, 1, tail=True) == [
        {"location_id": 2, "content": "Table 1. Deficiency summary"}
    ]


def test_pick_caption_prefers_table_label():
    before = [{"location_id": 1, "content": "Deficiency Profile"}]
    after = [{"location_id": 2, "content": "Table 1. Deficiency summary by process"}]
    assert pick_caption(before, after) == "Table 1. Deficiency summary by process"


def test_header_row_is_first_physical_row():
    el = DocElement(
        layer="table",
        location_id=1,
        path="/body/tbl[1]",
        props={},
        meta={
            "cells": [
                {"row": 2, "col": 1, "text": "body"},
                {"row": 1, "col": 2, "text": "High"},
                {"row": 1, "col": 1, "text": "Process"},
            ]
        },
    )
    assert header_row(el) == ["Process", "High"]


def test_joint_naming_fills_usage_when_llm_omits_it():
    tables = [
        [
            DocElement(
                layer="table",
                location_id=1,
                path="/body/tbl[1]",
                props={"table_format": {}},
                meta={
                    "section_index": 1,
                    "cells": [
                        {"row": 1, "col": 1, "text": "Control area"},
                        {"row": 1, "col": 2, "text": "Test objective"},
                    ],
                    "neighbor_after": [
                        {"location_id": 9, "content": "Table 3. Control testing matrix"}
                    ],
                },
            )
        ]
    ]
    sections = [
        [DocElement(layer="section", location_id=1, path="/section[1]", props={}, content="Body")]
    ]

    class LM:
        def chat_json(self, *, system, user, **kwargs):
            return {
                "styles": [
                    {
                        "object": "section",
                        "cluster_id": 1,
                        "style_name": "body",
                        "display_name": "Body",
                    },
                    {
                        "object": "table",
                        "cluster_id": 1,
                        "style_name": "grid",
                        "display_name": "Grid",
                    },
                ]
            }

    prompt = {
        "system": "name",
        "user_template": "Clusters:\n{{layers_json}}\nNotes:\n{{requirement}}",
    }
    catalog, decls = induce_catalog_from_all_layers(
        clusters_by_layer={"section": sections, "table": tables},
        language_model=LM(),
        prompt=prompt,
        llm_kwargs={},
    )
    tbl = next(e for e in catalog.entries if e.object == "table")
    assert tbl.captions == ["Table 3. Control testing matrix"]
    assert tbl.header_rows == ["Control area | Test objective"]
    assert tbl.caption_type == "Table 3. Control testing matrix"
    assert tbl.header_semantics == "Control area | Test objective"
    assert decls[tbl.style_id]["header_rows"] == ["Control area | Test objective"]
