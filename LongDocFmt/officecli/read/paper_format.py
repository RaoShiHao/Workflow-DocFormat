"""Page paper block normalization — width/height only (no ``orientation``)."""

from __future__ import annotations

from typing import Any

_PAPER_KEYS = ("size", "width", "height")


def normalize_paper_block(paper: dict[str, Any] | None) -> dict[str, Any]:
    """
    Keep only writable paper fields.

    ``orientation`` / ``w:orient`` is dropped: it may be stale in OOXML and
    effective layout is defined by ``width`` + ``height`` absolute values.
    """
    if not paper:
        return {}
    return _omit_none({key: paper.get(key) for key in _PAPER_KEYS if key in paper})


def normalize_page_format(page_format: dict[str, Any] | None) -> dict[str, Any]:
    """Strip unsupported ``paper.orientation`` before compare/migrate/write."""
    if not page_format:
        return {}
    out = dict(page_format)
    if "paper" in out:
        out["paper"] = normalize_paper_block(out.get("paper"))
    return out


def page_format_for_dataset_migration(page_format: dict[str, Any] | None) -> dict[str, Any]:
    """
    Page props safe to copy from template style → mapped content section.

    ``section_type`` (nextPage / continuous / …) describes how *this* section
    starts relative to the previous one. It is tied to document structure, not
    reusable page styling (margins, headers, page numbers). Copying it when
    template section N maps to content section M would rewrite content breaks
    (e.g. cover style ``continuous`` on template §1 → content §2).
    """
    out = normalize_page_format(page_format)
    out.pop("section_type", None)
    return out


def _omit_none(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if v is not None}
