"""Unit tests for page number section override resolution."""

from __future__ import annotations

from LongDocFormatter.officecli.read.page_schema import page_number_section_overrides


def test_page_number_overrides_infers_decimal_from_footer() -> None:
    page_format = {
        "footer": {
            "primary": {
                "present": True,
                "page_number": {
                    "start": 1,
                    "continue": False,
                    "alignment": "center",
                },
            }
        },
        "page_start": 1,
    }
    overrides = page_number_section_overrides(page_format)
    assert overrides["page_num_fmt"] == "decimal"
    assert overrides["page_start"] == 1


def test_page_number_overrides_keeps_explicit_roman() -> None:
    page_format = {
        "page_num_fmt": "lowerRoman",
        "footer": {
            "primary": {
                "present": True,
                "page_number": {"format": "lowerRoman", "start": 1},
            }
        },
    }
    overrides = page_number_section_overrides(page_format)
    assert overrides["page_num_fmt"] == "lowerRoman"


def test_page_number_overrides_empty_when_no_numbering() -> None:
    page_format = {
        "footer": {
            "primary": {"present": False},
        }
    }
    assert page_number_section_overrides(page_format) == {}


if __name__ == "__main__":
    test_page_number_overrides_infers_decimal_from_footer()
    test_page_number_overrides_keeps_explicit_roman()
    test_page_number_overrides_empty_when_no_numbering()
    print("ok")
