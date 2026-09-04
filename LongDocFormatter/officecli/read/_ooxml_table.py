"""Read table row OOXML properties not exposed by officecli get (e.g. w:cantSplit)."""

from __future__ import annotations

import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_NS = {"w": _W_NS}
_W_VAL = f"{{{_W_NS}}}val"


def _cant_split_active(tr: ET.Element) -> bool:
    """
  Return True when ``w:cantSplit`` is on (row must not break across pages).

  Maps to Word UI "Allow row to break across pages" **unchecked**.
  """
    tr_pr = tr.find("w:trPr", _NS)
    if tr_pr is None:
        return False
    el = tr_pr.find("w:cantSplit", _NS)
    if el is None:
        return False
    val = el.get(_W_VAL)
    if val is None:
        return True
    return str(val).strip().lower() not in {"0", "false", "off"}


def read_row_cant_split_flags(
    doc_path: str | Path,
    table_index: int = 1,
) -> list[bool]:
    """
    Per-row ``cantSplit`` flags for ``table_index`` (1-based).

    Returns one bool per row: True = cantSplit on (disallow break across pages).
    """
    doc_path = Path(doc_path)
    with zipfile.ZipFile(doc_path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))

    tables = root.findall(".//w:tbl", _NS)
    if table_index < 1 or table_index > len(tables):
        return []
    table = tables[table_index - 1]
    return [_cant_split_active(tr) for tr in table.findall("w:tr", _NS)]


def cant_split_to_allow_break(cant_split_on: bool) -> bool:
    """``allow_break_across_pages`` is the inverse of ``w:cantSplit``."""
    return not cant_split_on


def aggregate_allow_break_across_pages(flags: list[bool]) -> bool | None:
    """Table-level value when all rows agree; else first row's value."""
    if not flags:
        return None
    values = [cant_split_to_allow_break(f) for f in flags]
    if all(v == values[0] for v in values):
        return values[0]
    return values[0]
