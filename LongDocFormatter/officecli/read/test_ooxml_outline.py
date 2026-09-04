"""OOXML outlineLvl is on the linked style, not the paragraph pPr."""
from __future__ import annotations

import zipfile
from pathlib import Path

from LongDocFormatter.officecli.read._ooxml_outline import (
    body_outline_index,
    coerce_outline_lvl,
)

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_W14 = "http://schemas.microsoft.com/office/word/2010/wordml"

_STYLES = f"""<?xml version="1.0" encoding="UTF-8"?>
<w:styles xmlns:w="{_W}">
  <w:style w:type="paragraph" w:styleId="Normal">
    <w:name w:val="Normal"/>
  </w:style>
  <w:style w:type="paragraph" w:styleId="ParaHeading1">
    <w:name w:val="Heading 1"/>
    <w:basedOn w:val="Normal"/>
    <w:pPr>
      <w:keepNext/>
      <w:outlineLvl w:val="0"/>
    </w:pPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="ParaHeading2">
    <w:name w:val="Heading 2"/>
    <w:basedOn w:val="Normal"/>
    <w:pPr><w:outlineLvl w:val="1"/></w:pPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading3">
    <w:name w:val="heading 3"/>
  </w:style>
</w:styles>
"""

_DOCUMENT = f"""<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="{_W}" xmlns:w14="{_W14}">
  <w:body>
    <w:p w14:paraId="AAA1">
      <w:pPr><w:pStyle w:val="ParaHeading1"/></w:pPr>
      <w:r><w:t>Chapter 1</w:t></w:r>
    </w:p>
    <w:p w14:paraId="AAA2">
      <w:pPr><w:pStyle w:val="ParaHeading2"/></w:pPr>
      <w:r><w:t>Section</w:t></w:r>
    </w:p>
    <w:p w14:paraId="AAA3">
      <w:pPr><w:pStyle w:val="Heading3"/></w:pPr>
      <w:r><w:t>Subsection</w:t></w:r>
    </w:p>
    <w:p w14:paraId="AAA4">
      <w:pPr><w:pStyle w:val="Normal"/></w:pPr>
      <w:r><w:t>Body</w:t></w:r>
    </w:p>
  </w:body>
</w:document>
"""


def _write_docx(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/styles.xml", _STYLES)
        archive.writestr("word/document.xml", _DOCUMENT)


def test_merge_paragraph_props_uses_style_get_not_doc_defaults() -> None:
    from LongDocFormatter.workflow.whitelist import merge_paragraph_props

    para_fmt = {
        "style": "ParaHeading1",
        "effective.size": "16pt",
        "effective.size.src": "/styles/ParaHeading1",
        "effective.font.eastAsia": "等线",
        "effective.font.eastAsia.src": "/docDefaults",
        "effective.font.ascii": "Georgia",
        "effective.font.ascii.src": "/styles/ParaHeading1",
    }
    style_fmt = {
        "size": "16pt",
        "bold": True,
        "outlineLvl": 0,
        "keepNext": True,
        "font.ascii": "Georgia",
        "align": "left",
    }
    merged = merge_paragraph_props(para_fmt, style_fmt)
    assert merged["outlineLvl"] == 0
    assert merged["keepNext"] is True
    assert merged["font.latin"] == "Georgia"
    assert "font.ea" not in merged
    assert coerce_outline_lvl(0) == 0
    assert coerce_outline_lvl("1") == 1
    assert coerce_outline_lvl(None) is None


def test_style_inherited_outline_and_builtin_heading_id(tmp_path: Path) -> None:
    doc = tmp_path / "t.docx"
    _write_docx(doc)
    index = body_outline_index(doc)
    assert index.lookup("/body/p[@paraId=AAA1]", location_id=1) == 0
    assert index.lookup_props("/body/p[@paraId=AAA1]", location_id=1).get("keepNext") is True
    assert index.lookup("/body/p[@paraId=AAA2]", location_id=2) == 1
    assert index.lookup("/body/p[@paraId=AAA3]", location_id=3) == 2
    assert index.lookup("/body/p[@paraId=AAA4]", location_id=4) is None

