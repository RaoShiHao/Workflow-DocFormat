"""Read document-level settings from ``word/settings.xml``."""

from __future__ import annotations

import zipfile
from functools import lru_cache
from pathlib import Path
from xml.etree import ElementTree as ET

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_NS = {"w": _W_NS}


def _settings_flag_present(root: ET.Element, local_name: str) -> bool:
    """
    True when a Word settings element is present and not explicitly off.

    Empty elements (``<w:evenAndOddHeaders/>``) mean enabled. ``w:val="0"|"false"``
    means disabled when present.
    """
    node = root.find(f"w:{local_name}", _NS)
    if node is None:
        return False
    raw = node.get(f"{{{_W_NS}}}val")
    if raw is None:
        return True
    return str(raw).strip().lower() not in {"0", "false", "off"}


@lru_cache(maxsize=32)
def _settings_flags(doc_path: str) -> dict[str, bool]:
    path = Path(doc_path)
    with zipfile.ZipFile(path) as archive:
        if "word/settings.xml" not in archive.namelist():
            return {"even_and_odd_headers": False}
        root = ET.fromstring(archive.read("word/settings.xml"))
    return {
        "even_and_odd_headers": _settings_flag_present(root, "evenAndOddHeaders"),
    }


def read_even_and_odd_headers(doc_path: str | Path) -> bool:
    """
    Whether Word has「奇偶页不同」enabled (``w:evenAndOddHeaders`` in settings).

    Residual ``headerReference type=even`` parts do **not** imply this flag; Word
    ignores those slots until settings enables odd/even headers.
    """
    return bool(_settings_flags(str(Path(doc_path).resolve()))["even_and_odd_headers"])
