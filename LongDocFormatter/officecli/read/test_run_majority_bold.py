"""Tests for paragraph main-font majority from multi-run text."""

from __future__ import annotations

from LongDocFormatter.officecli.read.text_reader import (
    _build_distinct_runs,
    _build_main_font_from_runs,
    _build_text_format,
)


def test_short_bold_label_long_plain_body_main_not_bold() -> None:
    """Template-style 'Label: long body' — main bold follows character majority."""
    para_fmt = {"bold": True}  # Word often marks pPr/rPr bold even when body is plain
    run_nodes = [
        {"type": "run", "text": "Standardized and Efficient Review: ", "format": {"bold": True}},
        {
            "type": "run",
            "text": (
                "All environmental restoration projects shall be reviewed and "
                "permitted in strict accordance with federal unified standards."
            ),
            "format": {},
        },
    ]
    main_base, main_adv, base_dom, _adv_dom = _build_main_font_from_runs(
        para_fmt, run_nodes
    )
    assert base_dom["bold"] is False
    assert main_base["bold"] is False

    runs = _build_distinct_runs(run_nodes, main_base, main_adv)
    assert len(runs) == 1
    assert runs[0]["base_font"]["bold"] is True
    assert "Standardized" in (runs[0].get("text") or "")


def test_text_format_merge_runs_keeps_bold_on_minority_run() -> None:
    tf = _build_text_format(
        {"bold": True, "size": "12pt"},
        run_nodes=[
            {"type": "run", "text": "Title: ", "format": {"bold": True}},
            {"type": "run", "text": "x" * 80, "format": {}},
        ],
        merge_runs=True,
    )
    assert (tf.get("base_font") or {}).get("bold") is False
    runs = tf.get("runs") or []
    assert runs and (runs[0].get("base_font") or {}).get("bold") is True
