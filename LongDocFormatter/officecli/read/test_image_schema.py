"""Tests for image format schema normalize (flat officecli vs nested size)."""

from __future__ import annotations

from LongDocFormatter.officecli.read.image_schema import (
    build_image_format,
    image_format_to_officecli_props,
)
from LongDocFormatter.workflow.migration_property_scope import (
    image_format_config_for_modify,
    prune_image_object,
)


def test_build_image_format_accepts_flat_officecli() -> None:
    assert build_image_format({"width": "12cm", "height": "8cm"}) == {
        "size": {"width": "12cm", "height": "8cm"}
    }


def test_build_image_format_accepts_nested_size() -> None:
    nested = {"size": {"width": "12.0cm", "height": "8.0cm"}}
    assert build_image_format(nested) == {
        "size": {"width": "12.0cm", "height": "8.0cm"}
    }


def test_image_format_config_for_modify_preserves_nested_size() -> None:
    fc = {
        "image_format": {
            "size": {"width": "12.0cm", "height": "8.0cm"},
            "metadata": {"id": 1},
        },
        "host_paragraph": {
            "path": "/body/p[1]",
            "alignment": {"alignment": "center"},
            "pagination_control": {"keep_with_next": True, "widow_control": False},
            "base_font": {"size": "12pt"},
        },
    }
    out = image_format_config_for_modify(fc)
    assert out["image_format"]["size"]["width"] == "12.0cm"
    assert out["image_format"]["size"]["height"] == "8.0cm"
    assert "metadata" not in out["image_format"]
    assert out["host_paragraph"]["alignment"] == {"alignment": "center"}
    assert out["host_paragraph"]["pagination_control"]["keep_with_next"] is True
    assert "path" not in out["host_paragraph"]
    assert "base_font" not in out["host_paragraph"]

    props, warnings = image_format_to_officecli_props(out["image_format"])
    assert props == {"width": "12cm", "height": "8cm"}
    assert warnings == []


def test_prune_image_object_keeps_nested_size() -> None:
    sample = {
        "path": "/body/p[1]/r[1]",
        "image_format": {
            "size": {"width": "5cm", "height": "3cm"},
            "metadata": {"id": 1},
        },
        "host_paragraph": {
            "path": "/body/p[1]",
            "alignment": {"alignment": "center"},
            "pagination_control": {"keep_with_next": True},
            "base_font": {"size": "12pt"},
        },
        "metadata": {"rel_id": "rId5"},
        "preview": "img",
    }
    pruned = prune_image_object(sample)
    assert pruned["image_format"] == {"size": {"width": "5cm", "height": "3cm"}}
    assert pruned["host_paragraph"]["alignment"] == {"alignment": "center"}
    assert "base_font" not in pruned["host_paragraph"]
    assert "metadata" not in pruned
    assert "preview" not in pruned
