"""Unit tests for dataset page format normalization."""

from __future__ import annotations

from LongDocFormatter.officecli.read.paper_format import page_format_for_dataset_migration


def test_page_format_for_dataset_migration_strips_section_type() -> None:
    src = {
        "margin": {"top": "2cm"},
        "section_type": "continuous",
        "paper": {"width": "21cm", "height": "29.7cm", "orientation": "landscape"},
    }
    out = page_format_for_dataset_migration(src)
    assert "section_type" not in out
    assert out["margin"]["top"] == "2cm"
    assert "orientation" not in (out.get("paper") or {})


if __name__ == "__main__":
    test_page_format_for_dataset_migration_strips_section_type()
    print("ok")
