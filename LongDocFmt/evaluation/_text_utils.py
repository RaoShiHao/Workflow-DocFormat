"""Text helpers for integrity (no dataset-tag stripping)."""

from __future__ import annotations

import re

_CELL_TEXT_RE = re.compile(r"[\r\x07]+")


def normalize_visible_text(text: str | None) -> str:
    if not text:
        return ""
    return _CELL_TEXT_RE.sub("", str(text)).strip()


def has_meaningful_text(text: str | None) -> bool:
    return bool(normalize_visible_text(text))
