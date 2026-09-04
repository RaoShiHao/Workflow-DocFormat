"""Integrity stream from query rows (no officecli)."""
from __future__ import annotations

from LongDocFormatter.evaluation.integrity import content_stream_from_query_rows


def test_content_stream_keeps_body_text_skips_cells_and_headers() -> None:
    paras = [
        {"path": "/body/p[1]", "text": "Hello"},
        {"path": "/body/tbl[1]/tr[1]/tc[1]/p[1]", "text": "cell"},
        {"path": "/header/p[1]", "text": "hdr"},
        {"path": "/body/p[2]", "text": "  "},
    ]
    tokens = content_stream_from_query_rows(paras, [])
    assert tokens == [("text", "/body/p[1]", "Hello")]


def test_content_stream_inline_image_on_empty_host_para() -> None:
    paras = [
        {"path": "/body/p[1]", "text": ""},
        {"path": "/body/p[2]", "text": "after"},
    ]
    pics = [
        {"path": "/body/p[1]/r[1]/drawing[1]", "format": {}},
        {"path": "/body/p[2]/r[1]/drawing[1]", "format": {"wrap": "inline"}},
    ]
    tokens = content_stream_from_query_rows(paras, pics)
    assert tokens[0] == ("image", 1, "/body/p[1]")
    assert tokens[1] == ("text", "/body/p[2]", "after")


def test_content_stream_skips_floating_pictures() -> None:
    paras = [{"path": "/body/p[1]", "text": ""}]
    pics = [{"path": "/body/p[1]/r[1]/drawing[1]", "format": {"anchor": True}}]
    assert content_stream_from_query_rows(paras, pics) == []
