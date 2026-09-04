"""Designed table-cell layer: slots + plan, not specimen r1_cN."""
from __future__ import annotations

from LongDocFormatter.workflow.apply_core import compile_ops
from LongDocFormatter.workflow.assignment import _overlay_llm_cells
from LongDocFormatter.workflow.catalog import cluster_elements, signature_of
from LongDocFormatter.workflow.cell_plan import classify_cells, coerce_slot, designed_slots, infer_table_style
from LongDocFormatter.workflow.contracts import Catalog, CatalogEntry, DocElement
from LongDocFormatter.workflow.declarations import _declaration_payload, _normalize_decl, declarations_from_exemplars


def _table(loc: int, cells: list[dict], tf: dict | None = None) -> DocElement:
    return DocElement(
        layer="table",
        location_id=loc,
        path=f"/body/table[{loc}]",
        props={"table_format": tf or {"align": "center"}, "cells": {}},
        content="",
        meta={"cells": cells, "n_rows": max(c["row"] for c in cells), "n_cols": max(c["col"] for c in cells)},
    )


def _cell(row: int, col: int, **chrome) -> dict:
    return {"row": row, "col": col, "text": f"{row}:{col}", "chrome": chrome, "path": f"/tc[{row}][{col}]"}


def test_infer_label_value_and_stub_plan():
    cover = _table(
        1,
        [
            _cell(1, 1, valign="center"),
            _cell(1, 2, valign="center", **{"border.bottom": "single"}),
            _cell(2, 1, valign="center"),
            _cell(2, 2, valign="center", **{"border.bottom": "single"}),
        ],
    )
    designed = infer_table_style(cover.to_dict())
    assert designed["cell_style_plan"]["mode"] == "label_value"
    assert set(designed["cells"]) == {"label", "value"}
    assert not any(str(k).startswith("r") for k in designed["cells"])

    header = {"fill": "#EEE", "valign": "center"}
    data = {"valign": "center"}
    grid = _table(
        2,
        [
            _cell(1, 1, **header),
            _cell(1, 2, **header),
            _cell(1, 3, **header),
            _cell(2, 1, **data),
            _cell(2, 2, **data),
            _cell(2, 3, **data),
        ],
    )
    sids = {"2:1:1": "ParaH", "2:1:2": "ParaH", "2:1:3": "ParaH", "2:2:1": "ParaStub", "2:2:2": "ParaData", "2:2:3": "ParaData"}
    designed = infer_table_style(grid.to_dict(), cell_para_sids=sids)
    assert designed["cell_style_plan"].get("header_row") is True
    assert designed["cell_style_plan"]["column_styles"][0] == "stub"
    assert designed["cell_paragraphs"]["stub"] == "ParaStub"
    assert designed["cell_paragraphs"]["data"] == "ParaData"


def test_cluster_tables_by_designed_slots_not_grid_size():
    header = {"fill": "#EEE"}
    data = {"valign": "center"}
    small = _table(1, [_cell(1, 1, **header), _cell(1, 2, **header), _cell(2, 1, **data), _cell(2, 2, **data)])
    wide = _table(
        2,
        [_cell(1, c, **header) for c in range(1, 6)] + [_cell(2, c, **data) for c in range(1, 6)],
    )
    a = signature_of(small.props, "table", element=small)
    b = signature_of(wide.props, "table", element=wide)
    assert a == b
    groups = cluster_elements([small, wide])
    assert len(groups) == 1
    assert len(groups[0]) == 2


def test_classify_cells_stays_inside_declared_slots():
    spec = {
        "cells": {"header": {"fill": "#EEE"}, "stub": {"valign": "center"}, "data": {"valign": "center"}},
        "cell_style_plan": {"mode": "column", "header_row": True, "column_styles": ["stub", "data", "data"]},
        "cell_paragraphs": {"header": "ParaH", "stub": "ParaS", "data": "ParaD"},
    }
    cells = [_cell(1, 1), _cell(1, 2), _cell(1, 3), _cell(2, 1), _cell(2, 2), _cell(2, 3), _cell(2, 8)]
    mapped = classify_cells(cells, spec)
    assert {m["cell_style"] for m in mapped} <= {"header", "stub", "data"}
    assert next(m for m in mapped if m["row"] == 1)["cell_style"] == "header"
    assert next(m for m in mapped if m["row"] == 2 and m["col"] == 1)["cell_style"] == "stub"
    extra = next(m for m in mapped if m["col"] == 8)
    assert extra["cell_style"] == "data"
    assert extra["paragraph_style"] == "ParaD"


def test_coerce_slot_and_llm_overlay():
    assert coerce_slot("head", {"header", "data"}) == "header"
    assert coerce_slot("r1_c1", {"header", "data"}) == "data"
    tbl = _table(1, [_cell(1, 1, fill="#EEE"), _cell(2, 1, valign="center")])
    spec = {
        "cells": {"header": {"fill": "#EEE"}, "data": {"valign": "center"}},
        "cell_style_plan": {"mode": "row", "header_row": True, "row_styles": ["header", "data"]},
        "cell_paragraphs": {"header": "ParaH", "data": "ParaD"},
    }
    entries = [
        CatalogEntry("ParaH", "paragraph.table_cell", "Header", ""),
        CatalogEntry("ParaD", "paragraph.table_cell", "Data", ""),
    ]
    lookup = {"ParaH": "ParaH", "ParaD": "ParaD", "Header": "ParaH"}
    mapped = _overlay_llm_cells(
        tbl,
        spec,
        [{"row": 2, "col": 1, "cell_style": "body", "paragraph_style": "ParaD"}],
        para_entries=entries,
        lookup=lookup,
        default_para="ParaD",
    )
    assert len(mapped) == 1
    assert mapped[0]["row"] == 2
    assert mapped[0]["cell_style"] == "data"


def test_declarations_use_designed_slots():
    header = {"fill": "#EEE", "valign": "center"}
    data = {"valign": "center"}
    tbl = _table(1, [_cell(1, 1, **header), _cell(1, 2, **header), _cell(2, 1, **data), _cell(2, 2, **data)])
    catalog = Catalog(
        entries=[
            CatalogEntry("TblGrid", "table", "Grid", "", exemplar_location_id=1, exemplar_path=tbl.path),
        ]
    )
    decls = declarations_from_exemplars(catalog, layer_elements={"table": [tbl], "paragraph.table_cell": []})
    spec = decls["TblGrid"]
    assert "r1_c1" not in spec["cells"]
    assert "header" in spec["cells"]
    assert spec.get("cell_style_plan")
    payload = _declaration_payload(catalog.entries[0], tbl)
    assert "header" in payload["cells"]


def test_normalize_text_decl_drops_physical_keys_and_adds_plan():
    spec = _normalize_decl(
        "table",
        {
            "table_format": {"align": "center"},
            "cells": {
                "header": {"fill": "#EEE"},
                "data": {"valign": "center"},
                "r1_c1": {"fill": "#111"},
            },
        },
    )
    assert "r1_c1" not in spec["cells"]
    assert spec["cell_style_plan"]["header_row"] is True


def test_designed_slots_keeps_row_last():
    spec = {
        "cells": {"header": {"fill": "#EEE"}, "data": {"valign": "center"}, "row_last": {"fill": "#DDD"}},
        "cell_style_plan": {"mode": "row", "header_row": True, "row_last": True, "row_styles": ["header", "data"]},
    }
    assert "row_last" in designed_slots(spec)
    assert "r1_c1" not in designed_slots({**spec, "cells": {**spec["cells"], "r1_c1": {"fill": "#000"}}})


def test_compile_tables_paints_only_llm_cells():
    inventory = {
        "table": [
            {
                "location_id": 1,
                "path": "/body/tbl[1]",
                "props": {"table_format": {}},
                "meta": {
                    "cells": [
                        {"row": 1, "col": 1, "path": "/body/tbl[1]/row[1]/cell[1]", "chrome": {}},
                        {"row": 1, "col": 2, "path": "/body/tbl[1]/row[1]/cell[2]", "chrome": {}},
                        {"row": 2, "col": 1, "path": "/body/tbl[1]/row[2]/cell[1]", "chrome": {}},
                        {"row": 2, "col": 8, "path": "/body/tbl[1]/row[2]/cell[8]", "chrome": {}},
                    ]
                },
            }
        ]
    }
    props = {
        "TblGrid": {
            "object": "table",
            "table_format": {"align": "center"},
            "cells": {"header": {"fill": "#EEE", "valign": "center"}, "data": {"valign": "center"}},
            "cell_style_plan": {"mode": "row", "header_row": True, "row_styles": ["header", "data"]},
            "cell_paragraphs": {"header": "ParaH", "data": "ParaD"},
        },
        "ParaH": {"object": "paragraph.table_cell", "props": {"bold": True}},
        "ParaD": {"object": "paragraph.table_cell", "props": {"bold": False}},
    }
    catalog = [{"style_id": "TblGrid", "object": "table", "display_name": "Grid"}]
    unlabeled = compile_ops(
        catalog_entries=catalog,
        props=props,
        loc={"by_layer": {"table": {"1": "TblGrid"}}, "table_cells": {}},
        inventory=inventory,
    )
    unlabeled_sets = [c for c in unlabeled.commands if c.get("command") == "set"]
    assert not any("/cell[" in str(c.get("path", "")) for c in unlabeled_sets)

    labeled = compile_ops(
        catalog_entries=catalog,
        props=props,
        loc={
            "by_layer": {"table": {"1": "TblGrid"}},
            "table_cells": {
                "1": [
                    {"row": 1, "col": 1, "cell_style": "header", "paragraph_style": "ParaH"},
                    {"row": 2, "col": 1, "cell_style": "data", "paragraph_style": "ParaD"},
                ]
            },
        },
        inventory=inventory,
    )
    sets = [c for c in labeled.commands if c.get("command") == "set"]
    header = next(c for c in sets if c.get("path") == "/body/tbl[1]/row[1]/cell[1]")
    assert header["props"].get("fill") in {"#EEE", "EEE", "#eee", "eee"}
    assert not any(c.get("path") == "/body/tbl[1]/row[2]/cell[8]" for c in sets)


def test_require_json_list_fails_on_truncated_table_json():
    from LongDocFormatter.workflow.assignment import _require_json_list
    from LongDocFormatter.workflow.json_util import LlmJsonParseError, parse_llm_json_strict

    truncated = {
        "content": (
            '{\n  "tables": [\n'
            '    {"location_id": 1, "table_style": "TblCoverInfo", "cells": [\n'
            '      {"row": 1, "col": 1, "cell_style": "label"\n'
        )
    }
    try:
        _require_json_list(truncated, "tables", "assignments", "items", layer="table")
        assert False, "expected LlmJsonParseError"
    except LlmJsonParseError as err:
        assert err.layer == "table"

    valid = {"content": '{"tables":[{"location_id":2,"table_style":"TblThreeLine","cells":[]}]}'}
    rows = _require_json_list(valid, "tables", "assignments", "items", layer="table")
    assert rows[0]["table_style"] == "TblThreeLine"
    parsed = parse_llm_json_strict(valid, layer="table")
    assert parsed["tables"][0]["location_id"] == 2