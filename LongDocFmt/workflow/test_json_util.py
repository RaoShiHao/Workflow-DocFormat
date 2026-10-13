from __future__ import annotations

from LongDocFmt.workflow.json_util import (
    LlmJsonParseError,
    compact_json_dumps,
    keyed_records,
    parse_llm_json_strict,
    salvage_json_array_objects,
    salvage_partial_llm_json,
)


def test_salvage_json_array_objects_stops_at_incomplete():
    text = (
        "["
        '{"location_id":1,"paragraph_style":"ParaBody"},'
        '{"location_id":2,"paragraph_style":"ParaBody"},'
        '{"location_id":3,"paragraph_style":"Para'
    )
    items = salvage_json_array_objects(text)
    assert [x["location_id"] for x in items] == [1, 2]


def test_salvage_partial_llm_json_assignments():
    text = (
        '{"assignments":['
        '{"location_id":1,"paragraph_style":"ParaBody"},'
        '{"location_id":2,"paragraph_style":"Pa'
    )
    parsed = salvage_partial_llm_json(text)
    assert len(parsed["assignments"]) == 1
    assert parsed["assignments"][0]["location_id"] == 1


def test_parse_llm_json_strict_salvages_truncated():
    result = {
        "content": (
            '{"tables":['
            '{"location_id":1,"table_style":"TblGrid","cells":[]},'
            '{"location_id":2,"table_style":"Tbl'
        )
    }
    parsed = parse_llm_json_strict(result, layer="table", list_keys=("tables", "items"))
    assert len(parsed["tables"]) == 1
    assert parsed["tables"][0]["table_style"] == "TblGrid"


def test_parse_llm_json_strict_still_fails_when_nothing_complete():
    result = {"content": '{"tables":[{"location_id":1,"table_style":"Tbl",'}
    try:
        parse_llm_json_strict(result, layer="table", list_keys=("tables",))
        assert False, "expected LlmJsonParseError"
    except LlmJsonParseError as err:
        assert err.layer == "table"


def test_salvage_partial_llm_json_object_map():
    text = '{"loc":{"1":"SecBody","2":"SecCover","3":"Se'
    parsed = salvage_partial_llm_json(text)
    assert parsed["loc"]["1"] == "SecBody"
    assert parsed["loc"]["2"] == "SecCover"
    assert "3" not in parsed["loc"]


def test_parse_llm_json_strict_salvages_truncated_object():
    result = {"content": '{"paragraph.body":{"1":"ParaBody","2":"Pa'}
    parsed = parse_llm_json_strict(result, layer="paragraph")
    assert parsed["paragraph.body"]["1"] == "ParaBody"


def test_keyed_records_accepts_list_and_map():
    rows = keyed_records(
        {"SecCover": {"object": "section", "display_name": "Cover"}},
        id_key="style_id",
    )
    assert rows == [{"object": "section", "display_name": "Cover", "style_id": "SecCover"}]
    legacy = keyed_records(
        [{"style_id": "ParaBody", "object": "paragraph.body"}],
        id_key="style_id",
    )
    assert legacy[0]["style_id"] == "ParaBody"


def test_compact_json_dumps_has_no_spaces():
    assert compact_json_dumps({"a": 1, "b": [2]}) == '{"a":1,"b":[2]}'
