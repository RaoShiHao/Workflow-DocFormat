"""Unit tests for header/footer distance read mapping."""

from __future__ import annotations

from LongDocFormatter.officecli.read.page_reader import _build_header_footer_layout


def test_header_footer_layout_reads_margin_header_footer() -> None:
    layout = _build_header_footer_layout(
        {"marginHeader": "1.5cm", "marginFooter": "1.75cm"}
    )
    assert layout == {
        "header_distance": "1.5cm",
        "footer_distance": "1.75cm",
    }


def test_header_footer_layout_prefers_explicit_distance_keys() -> None:
    layout = _build_header_footer_layout(
        {
            "headerDistance": "2cm",
            "footerDistance": "2.5cm",
            "marginHeader": "1.5cm",
            "marginFooter": "1.5cm",
        }
    )
    assert layout["header_distance"] == "2cm"
    assert layout["footer_distance"] == "2.5cm"


if __name__ == "__main__":
    test_header_footer_layout_reads_margin_header_footer()
    test_header_footer_layout_prefers_explicit_distance_keys()
    print("ok")
