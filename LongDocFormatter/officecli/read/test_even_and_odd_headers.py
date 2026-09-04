"""Odd/even header flag must follow settings.xml, not residual even parts."""

from __future__ import annotations

from pathlib import Path

from LongDocFormatter.officecli.read._ooxml_settings import read_even_and_odd_headers
from LongDocFormatter.officecli.read.page_reader import WordPageReader

ROOT = Path(__file__).resolve().parents[3]
THESIS_IMAGE = ROOT / "templates" / "zh" / "thesis-image" / "2-i" / "template.docx"
INIT_481 = ROOT / "FormatBench-experiment" / "481" / "init.docx"
GOV_DOC = ROOT / "templates" / "zh" / "gov_doc" / "1-i" / "template.docx"


def test_settings_flag_false_when_even_parts_exist_without_setting() -> None:
    if not THESIS_IMAGE.is_file():
        return
    assert read_even_and_odd_headers(THESIS_IMAGE) is False
    section = WordPageReader(THESIS_IMAGE).read_section_at(1)
    assert section is not None
    header = (section.page_format or {}).get("header") or {}
    footer = (section.page_format or {}).get("footer") or {}
    assert header.get("different_odd_even") is False
    assert footer.get("different_odd_even") is False
    assert (header.get("even") or {}).get("present") is False
    assert (footer.get("even") or {}).get("present") is False


def test_sample_481_init_does_not_enable_odd_even() -> None:
    if not INIT_481.is_file():
        return
    assert read_even_and_odd_headers(INIT_481) is False
    section = WordPageReader(INIT_481).read_section_at(1)
    assert section is not None
    header = (section.page_format or {}).get("header") or {}
    assert header.get("different_odd_even") is False
    # Residual even header XML must not surface as an active even slot.
    assert (header.get("even") or {}).get("present") is False


def test_gov_doc_keeps_odd_even_when_settings_enabled() -> None:
    if not GOV_DOC.is_file():
        return
    assert read_even_and_odd_headers(GOV_DOC) is True
    section = WordPageReader(GOV_DOC).read_section_at(1)
    assert section is not None
    footer = (section.page_format or {}).get("footer") or {}
    assert footer.get("different_odd_even") is True
    assert (footer.get("even") or {}).get("present") is True
