"""Helpers for inventory read-path reuse (no officecli)."""
from __future__ import annotations

from LongDocFormatter.workflow.officecli_doc import (
    _index_query_rows,
    _is_table_cell_element,
    _parent_para_path,
    _path_is_table_cell_para,
    _row_for_path,
    _runs_from_query,
    _tbl_cell_key,
)
from LongDocFormatter.workflow.contracts import DocElement


def test_parent_para_path_strips_run_tail() -> None:
    assert _parent_para_path("/body/p[3]/r[2]") == "/body/p[3]"
    assert _parent_para_path("/body/p[3]/w:r[1]/w:t[1]") == "/body/p[3]"


def test_cell_para_paths_are_detected() -> None:
    assert _path_is_table_cell_para("/body/table[1]/tr[1]/tc[2]/p[1]")
    assert not _path_is_table_cell_para("/body/p[4]")


def test_para_index_matches_normalized_table_alias() -> None:
    rows = [{"path": "/body/tbl[1]/tr[1]/tc[1]/p[1]", "format": {"align": "center"}, "text": "A"}]
    index = _index_query_rows(rows)
    hit = _row_for_path(index, "/body/table[1]/tr[1]/tc[1]/p[1]")
    assert hit is not None
    assert hit["format"]["align"] == "center"


def test_para_index_matches_paraid_to_p1() -> None:
    rows = [
        {
            "path": "/body/tbl[1]/tr[1]/tc[1]/p[@paraId=00100002]",
            "format": {"align": "center"},
            "text": "A",
        }
    ]
    index = _index_query_rows(rows)
    hit = _row_for_path(index, "/body/tbl[1]/tr[1]/tc[1]/p[1]")
    assert hit is not None
    assert _tbl_cell_key("/body/table[1]/tr[2]/tc[3]/p[@paraId=ab]") == "1:2:3"


def test_only_real_tc_nodes_count_as_cells() -> None:
    assert _is_table_cell_element("/body/tbl[1]/tr[1]/tc[2]", "cell")
    assert _is_table_cell_element("/body/tbl[1]/tr[1]/tc[2]", "")
    assert not _is_table_cell_element("/body/tbl[1]/tr[1]/tc[2]/p[1]/r[1]", "run")
    assert not _is_table_cell_element("/body/tbl[1]/tr[1]/tc[2]/p[@paraId=x]", "paragraph")


def test_runs_from_query_keeps_only_deltas_in_para_order() -> None:
    paras = [
        DocElement(layer="paragraph.body", location_id=1, path="/body/p[1]", props={"size": "12", "bold": False}),
        DocElement(layer="paragraph.body", location_id=2, path="/body/p[2]", props={"size": "12", "bold": False}),
    ]
    runs = [
        {"path": "/body/p[1]/r[1]", "text": "aa", "format": {"size": "12", "bold": False}},
        {"path": "/body/p[1]/r[2]", "text": "BB", "format": {"size": "12", "bold": True}},
        {"path": "/body/table[1]/tr[1]/tc[1]/p[1]/r[1]", "text": "cell", "format": {"bold": True}},
        {"path": "/body/p[2]/r[1]", "text": "cc", "format": {"size": "14", "bold": False}},
    ]
    out = _runs_from_query(paras, runs)
    assert [el.location_id for el in out] == [1, 2]
    assert out[0].props == {"bold": True}
    assert out[0].meta["para_location_id"] == 1
    assert out[1].props == {"size": "14"}
    assert out[1].meta["para_location_id"] == 2


def test_inventory_profile_for_stem() -> None:
    from LongDocFormatter.workflow.input_cache import inventory_profile_for_stem

    assert inventory_profile_for_stem("source") == "assign"
    assert inventory_profile_for_stem("template") == "full"


def test_section_previews_unusable_on_empty_or_collapsed():
    from LongDocFormatter.workflow.contracts import DocElement
    from LongDocFormatter.workflow.input_cache import (
        _section_previews_collapsed,
        _section_previews_unusable,
    )

    def _secs(*texts: str):
        return {
            "section": [
                DocElement(layer="section", location_id=i, path=f"/section[{i}]", props={}, content=t)
                for i, t in enumerate(texts, start=1)
            ]
        }

    assert _section_previews_unusable(_secs("", "", "")) is True
    assert _section_previews_unusable(_secs("cover", "notice", "body")) is False
    same = "same preview " * 3
    collapsed = _secs(same, same, same)
    assert _section_previews_collapsed(collapsed) is True
    assert _section_previews_unusable(collapsed) is True
