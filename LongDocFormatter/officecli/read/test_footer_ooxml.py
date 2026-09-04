"""Footer OOXML alignment via section footerReference (not officecli path index)."""

from __future__ import annotations

from pathlib import Path

from data_process.page_migration import read_template_page_format
from LongDocFormatter.officecli.read.footer_ooxml import (
    alignment_for_section_footer_slot,
    footer_part_path_for_slot,
    footer_parts_by_section,
)

ROOT = Path(__file__).resolve().parents[3]
PAPER = ROOT / "templates" / "en" / "paper" / "1-i" / "template.docx"
GOV_DOC = ROOT / "templates" / "zh" / "gov_doc" / "1-i" / "template.docx"


def test_paper_section_footer_parts_map_to_correct_xml() -> None:
    if not PAPER.is_file():
        return
    parts = footer_parts_by_section(PAPER)
    assert len(parts) >= 2
    assert parts[0].get("default") == "word/footer2.xml"
    assert parts[1].get("default") == "word/footer4.xml"
    assert footer_part_path_for_slot(PAPER, 2, "primary") == "word/footer4.xml"


def test_paper_content_and_ref_sections_read_center_alignment() -> None:
    if not PAPER.is_file():
        return
    pf1 = read_template_page_format(PAPER, 1)
    pf2 = read_template_page_format(PAPER, 2)
    assert (pf1.get("footer") or {}).get("primary", {}).get("page_number", {}).get(
        "alignment"
    ) == "center"
    assert (pf2.get("footer") or {}).get("primary", {}).get("page_number", {}).get(
        "alignment"
    ) == "center"
    assert alignment_for_section_footer_slot(PAPER, 2, "primary") == "center"


def test_gov_doc_odd_even_footer_slots() -> None:
    if not GOV_DOC.is_file():
        return
    pf = read_template_page_format(GOV_DOC, 1)
    footer = pf.get("footer") or {}
    assert footer.get("different_odd_even") is True
    assert (footer.get("primary") or {}).get("present") is True
    assert (footer.get("even") or {}).get("present") is True
    assert (footer.get("even") or {}).get("page_number", {}).get("alignment") == "right"
    assert footer_part_path_for_slot(GOV_DOC, 1, "even") == "word/footer1.xml"
    assert alignment_for_section_footer_slot(GOV_DOC, 1, "even") == "right"


def test_normalize_odd_even_footer_inherits_from_primary() -> None:
    from LongDocFormatter.officecli.modify.page_writer import _normalize_odd_even_footer_slots

    stale = {
        "footer": {
            "different_odd_even": True,
            "primary": {
                "present": True,
                "page_number": {"alignment": "left", "start": 1},
            },
            "even": {"present": False},
        }
    }
    normalized = _normalize_odd_even_footer_slots(stale)
    even = (normalized.get("footer") or {}).get("even") or {}
    assert even.get("present") is True
    assert even.get("page_number", {}).get("alignment") == "right"


if __name__ == "__main__":
    test_paper_section_footer_parts_map_to_correct_xml()
    test_paper_content_and_ref_sections_read_center_alignment()
    print("ok")
