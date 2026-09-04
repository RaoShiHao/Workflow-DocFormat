"""Unit tests for table border write props (no officecli)."""

from __future__ import annotations

from LongDocFormatter.officecli.read.table_schema import (
    borders_for_cell_inherit,
    borders_to_officecli_props,
    cell_borders_to_officecli_props,
    compact_borders_for_migration,
    enrich_table_borders_from_perimeter_cells,
    table_borders_to_officecli_props,
)


def test_borders_to_officecli_props_skips_inner_grid() -> None:
    borders = {
        "top": {"style": "single", "size": 4},
        "horizontal": {"style": "single", "size": 4},
        "vertical": {"style": "none", "size": 0},
    }
    props = borders_to_officecli_props(borders)
    assert "border.top" in props
    assert "border.horizontal" not in props
    assert "border.vertical" not in props


def test_borders_for_cell_inherit() -> None:
    borders = {
        "top": {"style": "single", "size": 4},
        "horizontal": {"style": "none", "size": 0},
        "vertical": {"style": "none", "size": 0},
    }
    inherited = borders_for_cell_inherit(borders)
    assert "top" in inherited
    assert "horizontal" not in inherited
    assert "vertical" not in inherited


def test_borders_to_officecli_props_table_writes_inside_grid() -> None:
    borders = {
        "top": {"style": "none", "size": 0},
        "horizontal": {"style": "none", "size": 0},
        "vertical": {"style": "none", "size": 0},
    }
    props = borders_to_officecli_props(borders, target="table")
    assert props["border.top"] == "none"
    assert props["border.insideH"] == "none"
    assert props["border.insideV"] == "none"
    assert "border.horizontal" not in props


def test_cell_borders_inherit_when_template_unset() -> None:
    source = {
        "top": {"style": "single", "size": 4},
        "bottom": {"style": "single", "size": 4},
        "left": {"style": "single", "size": 4},
        "right": {"style": "single", "size": 4},
    }
    assert cell_borders_to_officecli_props({}, source_borders=source) == {}
    all_null = {
        "top": None,
        "bottom": None,
        "left": None,
        "right": None,
    }
    assert cell_borders_to_officecli_props(all_null, source_borders=source) == {}


def test_cell_borders_partial_template_writes_only_explicit_sides() -> None:
    template = {"bottom": {"style": "single", "size": 4}}
    source = {
        "top": {"style": "single", "size": 4},
        "bottom": {"style": "single", "size": 4},
        "left": {"style": "single", "size": 4},
        "right": {"style": "single", "size": 4},
    }
    props = cell_borders_to_officecli_props(template, source_borders={})
    assert props.get("border.bottom") == "single;4"
    assert "border.top" not in props
    assert "border.left" not in props


def test_cell_borders_explicit_none_clears_all_sides() -> None:
    template = {
        side: {"style": "none", "size": 0}
        for side in ("top", "bottom", "left", "right")
    }
    props = cell_borders_to_officecli_props(template, source_borders={})
    assert props == {
        "border.top": "none",
        "border.bottom": "none",
        "border.left": "none",
        "border.right": "none",
    }


def test_table_borders_inherit_when_template_unset() -> None:
    source = {
        "top": {"style": "single", "size": 4},
        "horizontal": {"style": "single", "size": 4},
        "vertical": {"style": "single", "size": 4},
    }
    assert borders_to_officecli_props({}, source_borders=source, target="table") == {}
    all_null = {
        side: None
        for side in ("top", "bottom", "left", "right", "horizontal", "vertical", "all")
    }
    assert borders_to_officecli_props(all_null, source_borders=source, target="table") == {}


def test_table_borders_partial_template_writes_only_explicit_sides() -> None:
    template = {
        "horizontal": {"style": "none", "size": 0},
        "vertical": {"style": "none", "size": 0},
    }
    source = {
        "top": {"style": "single", "size": 4},
        "horizontal": {"style": "single", "size": 4},
        "vertical": {"style": "single", "size": 4},
    }
    props = borders_to_officecli_props(template, source_borders=source, target="table")
    assert props == {
        "border.insideH": "none",
        "border.insideV": "none",
    }


def test_enrich_table_borders_from_perimeter_cells() -> None:
    table_format = {
        "style": "a3",
        "borders": {
            "horizontal": {"style": "none", "size": 0},
            "vertical": {"style": "none", "size": 0},
        },
    }
    rows = [
        {
            "cells": [
                {"cell_format": {"borders": {"top": {"style": "single", "size": 4}}}},
                {"cell_format": {"borders": {"top": {"style": "single", "size": 4}}}},
            ]
        },
        {
            "cells": [
                {"cell_format": {"borders": {"top": {"style": "single", "size": 4}}}},
                {"cell_format": {"borders": {"top": {"style": "single", "size": 4}}}},
            ]
        },
    ]
    borders = enrich_table_borders_from_perimeter_cells(table_format, rows)
    assert borders["top"] == {"style": "single", "size": 4}
    assert borders["horizontal"] == {"style": "none", "size": 0}


def test_compact_borders_inherit_returns_empty() -> None:
    all_null = {side: None for side in ("top", "bottom", "left", "right")}
    assert compact_borders_for_migration(all_null, target="cell") == {}
    assert compact_borders_for_migration({}, target="cell") == {}


if __name__ == "__main__":
    test_borders_to_officecli_props_skips_inner_grid()
    test_borders_for_cell_inherit()
    test_borders_to_officecli_props_table_writes_inside_grid()
    test_cell_borders_inherit_when_template_unset()
    test_cell_borders_partial_template_writes_only_explicit_sides()
    test_cell_borders_explicit_none_clears_all_sides()
    test_table_borders_inherit_when_template_unset()
    test_table_borders_partial_template_writes_only_explicit_sides()
    test_enrich_table_borders_from_perimeter_cells()
    test_compact_borders_inherit_returns_empty()
    print("ok")
