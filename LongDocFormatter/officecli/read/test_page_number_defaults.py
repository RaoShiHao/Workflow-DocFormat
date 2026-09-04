"""Unit tests for Path-B-aligned page number default enrichment."""

from __future__ import annotations

from pathlib import Path

import pytest

from LongDocFormatter.officecli.read import WordPageReader
from LongDocFormatter.officecli.read.page_schema import enrich_page_number_defaults, resolve_section_page_start


def test_enrich_single_section_defaults() -> None:
    enriched = enrich_page_number_defaults(
        {"alignment": "left", "name": "Times New Roman", "size": "12pt"},
        section_index=1,
        section_count=1,
        page_start_raw=None,
    )
    assert enriched["start"] == 1
    assert enriched["continue"] is False
    assert enriched["format"] == "decimal"


def test_enrich_skips_when_page_start_in_ooxml() -> None:
    original = {"alignment": "center"}
    enriched = enrich_page_number_defaults(
        original,
        section_index=2,
        section_count=6,
        page_start_raw=1,
    )
    assert enriched == original


def test_enrich_later_section_continues_without_page_start() -> None:
    enriched = enrich_page_number_defaults(
        {"alignment": "center"},
        section_index=3,
        section_count=6,
        page_start_raw=None,
    )
    assert enriched["continue"] is True
    assert "start" not in enriched


def test_resolve_section_page_start_from_enriched_footer() -> None:
    footer = {
        "primary": {
            "present": True,
            "page_number": {"start": 1, "continue": False, "format": "decimal"},
        }
    }
    assert resolve_section_page_start({}, footer) == 1


def test_gov_doc_template_extracts_page_number_defaults() -> None:
    root = Path(__file__).resolve().parents[3]
    template = root / "evaluation" / "test_case" / "gov_doc-case" / "template.docx"
    if not template.is_file():
        pytest.skip("gov_doc-case template not present")
    section = WordPageReader(template).read_section_at(1)
    assert section is not None
    pf = section.page_format
    assert pf.get("page_start") == 1
    primary = ((pf.get("footer") or {}).get("primary") or {}).get("page_number") or {}
    even = ((pf.get("footer") or {}).get("even") or {}).get("page_number") or {}
    assert primary.get("start") == 1
    assert primary.get("continue") is False
    assert even.get("start") == 1
    assert even.get("continue") is False


def test_thesis_cover_section_unchanged() -> None:
    root = Path(__file__).resolve().parents[3]
    template = root / "evaluation" / "test_case" / "thesis-case" / "template.docx"
    if not template.is_file():
        pytest.skip("thesis-case template not present")
    section = WordPageReader(template).read_section_at(1)
    assert section is not None
    pf = section.page_format
    assert pf.get("page_start") is None
    primary = ((pf.get("footer") or {}).get("primary") or {}).get("page_number") or {}
    assert not primary
